# ChEMBL local query tools

Command line tool and REST API (FastAPI + Swagger UI) for a local ChEMBL mirror in MariaDB/MySQL,
plus an **evidence layer** that extends ChEMBL's curated `drug_mechanism` table with every
compound–target pair that has measured activity data.

* `chembl.py`: all SQL, plus a CLI. Compound → targets, target → compounds, disease → compounds, evidence levels.
* `chembl_api.py`: REST API on top of `chembl.py`, Swagger UI at `/docs`.
* `chembl_mech_build.py`: builds the table `x_mech_candidates` and the view `x_mech_evidence` (stage 1 of the extension).
* `chembl-api.service`: example systemd unit.

Tested with ChEMBL 37 on MariaDB 11.8. ChEMBL data itself is not part of this repository.

## Why the extension

ChEMBL holds two very different kinds of compound–target information:

| layer | table | what it contains | size (ChEMBL 37) |
|---|---|---|---|
| curated mechanism | `drug_mechanism` | the mechanism of action of a drug, written by ChEMBL curators, mainly for approved or late-stage drugs | 5 954 compounds, 7 561 rows |
| measurements | `activities` | every published potency value (IC50, EC50, Ki, Kd, …) from the literature, patents and deposited screens | 24 million records, 3.4 million compound–target pairs with a pChEMBL value |

Anyone who asks "which compounds act on target X?" or "what does compound Y act on?" and only looks at
`drug_mechanism` sees **a small, selective subset**. That subset is not chosen by the data. It depends on
regulatory status and on curation workload:

* **Curation lag.** In ChEMBL 37, nirmatrelvir (the active ingredient of Paxlovid, approved) has no
  `drug_mechanism` row for the SARS-CoV-2 main protease, although `activities` holds 66 measurements from
  52 documents with a best pChEMBL of 9.59 (≈ 0.26 nM).
* **Status, not evidence.** Compounds that were tested but never approved, or whose clinical value is
  disputed, never get a curated mechanism, whatever their measured potency. They stay invisible in a
  mechanism-only view, and so do the gaps in their evidence.
* **Phenotypic results have no target.** Many antiviral and antiparasitic results are measured on the
  whole organism or a cell line ("inhibits SARS-CoV-2 replication in Vero E6 cells"). They exist only in
  `activities`, attached to an ORGANISM or CELL-LINE target.

The aim of the extension is to **show every candidate for which any measured evidence exists, and to grade
that evidence transparently**, instead of a binary "curated / not curated". A disputed compound then
appears with its real numbers: how potent, how often reproduced, in how many independent documents, on a
molecular target or only phenotypically. The reader judges the data, not the absence of a database row.

What the extension does **not** do: in vitro potency is not clinical efficacy. A pChEMBL of 5.7 in a cell
assay says nothing about whether a drug helps patients at a tolerable dose. Clinical outcome data is a
separate axis (see roadmap).

## Evidence levels

`chembl_mech_build.py` aggregates all activities with a pChEMBL value per (compound, target) into
`x_mech_candidates`. The view `x_mech_evidence` assigns one level per pair and adds every curated
`drug_mechanism` pair as level A.

| level | rule | pairs | compounds |
|---|---|---:|---:|
| **A** | curated in `drug_mechanism` | 6 941 | 5 398 |
| **B** | molecular target, assay confidence ≥ 8, best pChEMBL ≥ 7, active (pChEMBL ≥ 5) in ≥ 2 documents | 94 271 | 71 986 |
| **C** | molecular target, assay confidence ≥ 8, best pChEMBL ≥ 6 | 1 106 491 | 790 787 |
| **D** | molecular target, best pChEMBL ≥ 5 (≈ 10 µM) | 752 757 | 485 839 |
| **P** | phenotypic target (organism, cell line, tissue, …), best pChEMBL ≥ 5 | 469 002 | 251 912 |
| **-** | measured, best pChEMBL < 5 | 1 023 978 | 457 762 |

pChEMBL = −log10(molar IC50/EC50/Ki/Kd/…): 6 = 1 µM, 9 = 1 nM. Assay confidence 9 = direct single
protein target, 8 = homologous single protein. ChEMBL already leaves `pchembl_value` empty for records
flagged by `data_validity_comment`, so flagged values are excluded. The raw aggregates (counts, best and
mean pChEMBL, years, measurement types) are stored. The level is computed in the view, so the rules can
be changed without a rebuild.

## Example: SARS-CoV-2, curated only vs. with the extension

### 1. Curated only: `drug_mechanism` on the SARS-CoV-2 replicase polyprotein 1ab (contains Mpro, PLpro, RdRp)

```
$ ./chembl.py "Replicase polyprotein 1ab" --target \
      --organism "Severe acute respiratory syndrome coronavirus 2" --candidates A

evidence  compound      max_phase  best_pchembl  n_meas  n_docs_active  mechanism_of_action
A         REMDESIVIR    4          6.14          10      5              Replicase polyprotein 1ab inhibitor
A         RO-0622       3                                               Replicase polyprotein 1ab inhibitor
A         GALIDESIVIR   2                                               Replicase polyprotein 1ab inhibitor
```

Three compounds. The approved main-protease inhibitor nirmatrelvir is missing.

### 2. With the extension, same target, levels A–C, named compounds only

```
$ ./chembl.py "Replicase polyprotein 1ab" --target \
      --organism "Severe acute respiratory syndrome coronavirus 2" --candidates ABC --named

evidence (levels ABC, showing 15 of 64)
evidence  compound                max_phase  best_pchembl  n_meas  n_docs_active
A         REMDESIVIR              4          6.14          10      5
A         RO-0622                 3
A         GALIDESIVIR             2
B         ENSITRELVIR             3          10.89         3       3
B         PF-00835231             -1         10            11      11
B         NIRMATRELVIR            4          9.59          66      52
B         SIMEPREVIR              4          8.61          4       2
B         EBSELEN                 3          8             28      21
B         BAICALEIN               2          7.7           30      15
B         TIDEGLUSIB              2          7.1           9       7
C         IBUZATRELVIR            2          8.61          1       1
C         POMOTRELVIR             2          8.57          1       1
…
```

64 named compounds instead of 3. All compounds, named or not, at levels A–D: 3 194.

### 3. With the extension, phenotypic level P on the whole virus (target `CHEMBL4303835`)

This is where compounds without a molecular target appear, including the disputed ones. Ranked by best
pChEMBL among the 1 446 compounds with pChEMBL ≥ 5:

```
$ ./chembl.py CHEMBL4303835 --target --candidates P --limit 10000

rank  compound              max_phase  best_pchembl  n_meas  n_docs_active
  14  REMDESIVIR            4          8.9           141     58
  26  MOLNUPIRAVIR          3          8.49          11      5
  34  NIRMATRELVIR          4          8.37          103     42
  49  CAMOSTAT              3          8.12          7       5
 646  HYDROXYCHLOROQUINE    4          6.17          8       6
 774  CHLOROQUINE           4          5.95          13      8
 823  FAVIPIRAVIR           4          5.86          7       1
 936  IVERMECTIN            4          5.7           7       5
 952  NITAZOXANIDE          4          5.67          4       3
1202  LOPINAVIR             4          5.3           9       4
```

(`max_phase` is the highest phase for any indication, not for COVID-19.)

Nothing is hidden. Every compound is listed, with numbers that let the reader compare. Ivermectin, for
example, has a reproducible effect (5 documents) at about 2 µM in cell culture, while nirmatrelvir works
at low nanomolar concentrations in 42 documents. `/indications/COVID-19` additionally lists all
590 compounds that reached a COVID-19 clinical trial (ivermectin, hydroxychloroquine, fluvoxamine, … at
phase 3). Trial outcomes are not recorded there.

## Setup

```
pip install --user -r requirements.txt          # pymysql, fastapi, uvicorn
```

Credentials are read from a my.cnf file (`host`, `port`, `user`, `password`). The schema name is `chembl`.

| variable | default |
|---|---|
| `CHEMBL_CNF` | `~/.my.cnf` |
| `CHEMBL_CNF_SECTION` | first of `[chembl]`, `[wagodb]`, `[client]` that exists |
| `CHEMBL_DB` | `chembl` |

Build the evidence layer once after every ChEMBL import (about 1 minute for ChEMBL 37). It needs CREATE,
DROP and CREATE VIEW on the schema:

```
./chembl_mech_build.py
```

Note for MariaDB ≥ 11.x: import the ChEMBL dump into a schema created with
`CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci`. The default uca1400 collation treats strings that
differ only by zero-width characters as equal and breaks the unique keys of the dump.

## CLI

```
./chembl.py ivermectin                          # curated mechanism + all measured targets
./chembl.py ivermectin --human --min-n 2
./chembl.py ivermectin --detail CHEMBL4302      # single records on one target
./chembl.py ivermectin --candidates             # all targets with evidence level
./chembl.py iverm --search                      # name/synonym prefix search
./chembl.py ABCB1 --target --organism "Homo sapiens"
./chembl.py CHEMBL240 --target --min-phase 4    # approved drugs measured on hERG
./chembl.py CHEMBL240 --target --candidates AB --named
./chembl.py KCNH --target --search
```

Output as aligned text, `--csv` or `--json`.

## REST API

```
python3 -m uvicorn chembl_api:app --host 0.0.0.0 --port 9099
```

Swagger UI: `http://<host>:9099/docs`

| endpoint | purpose |
|---|---|
| `GET /compounds/search?q=` | prefix search over names and synonyms |
| `GET /compounds/{name}` | resolve a name, synonym or ChEMBL ID, list salts/forms |
| `GET /compounds/{name}/targets` | all measured targets, one row per target |
| `GET /compounds/{name}/mechanisms` | curated mechanism of action |
| `GET /compounds/{name}/activities?target=` | single activity records on one target |
| `GET /compounds/{name}/candidates?levels=` | every target with evidence level |
| `GET /compounds/{name}/indications` | indications with trial phase and references |
| `GET /targets/search?q=&organism=` | target prefix search (`q=%` lists all targets of an organism) |
| `GET /targets/{name}` | resolve ChEMBL ID, UniProt accession, gene symbol or name |
| `GET /targets/{name}/compounds` | all measured compounds, one row per compound |
| `GET /targets/{name}/mechanisms` | drugs with a curated mechanism on the target |
| `GET /targets/{name}/candidates?levels=` | every compound with evidence level |
| `GET /indications/{disease}` | all compounds that reached a trial for a disease (EFO/MeSH prefix) |
| `GET /version` | ChEMBL release |

Example timings on a desktop server (ChEMBL 37): target → 12 000 compounds 0.7 s, evidence queries
0.01–0.1 s, `/indications/COVID-19` 0.1 s.

## Roadmap

1. **Stage 1 (done):** evidence from ChEMBL `activities`, levels A–D and P.
2. **Stage 2:** further open curated sources in the same table with their own `source`:
   DrugCentral, IUPHAR/BPS Guide to Pharmacology, BindingDB.
3. **Stage 3:** literature and clinical outcome, as a separate axis: PubTator3 chemical–gene relations
   (text-mined, own level), ClinicalTrials.gov results, living meta-analyses such as COVID-NMA.

## Data licence

ChEMBL is published by EMBL-EBI under CC BY-SA 3.0. Cite: Zdrazil et al., *The ChEMBL Database in 2023*,
Nucleic Acids Res. 52 (2024) D1180–D1192.
