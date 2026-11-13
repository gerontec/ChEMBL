# ChEMBL query service

## ➜ Query form: <https://chembl.heissa.de/>

Open the link, type a name, press *Search*. No account, no installation, no programming. German and English.

* **Compound → targets**: what does ivermectin, hydroxychloroquine, nirmatrelvir … act on?
  [Example](https://chembl.heissa.de/?mode=compound&q=ivermectin)
* **Target → compounds**: which compounds act on P-glycoprotein (ABCB1), hERG, the SARS-CoV-2 main protease …?
  [Example](https://chembl.heissa.de/?mode=target&q=ABCB1&org=Homo+sapiens&approved=1)
* **Disease → compounds in trials**: every compound that reached a clinical trial, whatever the outcome.
  [Example](https://chembl.heissa.de/?mode=disease&q=COVID-19)

Every result can be downloaded as an **Excel file** with one click. Long lists (many names, more than
10 000 rows) run as a *large job* from the same page; the Excel file is offered for download when it is
ready, optionally also by mail.

The values are laboratory measurements (enzyme assays, cell culture) from the public ChEMBL database.
They say nothing about clinical efficacy, dose or safety in humans and are no treatment recommendation.

## What this is

A query service over the ChEMBL database (EMBL-EBI, ChEMBL 37) in both directions (compound → targets,
target → compounds, disease → compounds), with

* the **query form** above (PHP + Bootstrap, served without any third-party resources),
* a **REST API** with Swagger UI for programs: <https://chembl.heissa.de/docs> ([details](#for-programmers-rest-api)),
* a **command line tool** for a local copy of the database,
* an **evidence layer** that extends ChEMBL's curated `drug_mechanism` table with every compound–target
  pair that has measured activity data, graded A–D and P ([why](#why-the-extension)).

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

In the form: [target CHEMBL4523582, level A only](https://chembl.heissa.de/?mode=target&q=CHEMBL4523582&lv%5B%5D=A)

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

In the form: [levels A–C, named only](https://chembl.heissa.de/?mode=target&q=CHEMBL4523582&lv%5B%5D=A&lv%5B%5D=B&lv%5B%5D=C&named=1)

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

In the form: [level P, approved drugs](https://chembl.heissa.de/?mode=target&q=CHEMBL4303835&lv%5B%5D=P&approved=1)

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

## For programmers: REST API

Everything the form does is available as a REST API: <https://chembl.heissa.de/docs> (Swagger: pick an
endpoint, *Try it out*, *Execute*). Every answer is JSON; add `format=xlsx` to any GET request for an Excel
file. Links work in the browser too:

* <https://chembl.heissa.de/compounds/ivermectin/candidates>: everything ivermectin acts on, with evidence level
* <https://chembl.heissa.de/targets/ABCB1/compounds?organism=Homo%20sapiens&limit=50>: everything measured on P-glycoprotein
* <https://chembl.heissa.de/indications/COVID-19>: every compound that reached a COVID-19 trial

Every query is a plain HTTP GET and answers within about a second. With curl:

```
# compound -> every target with evidence level (A curated, B/C/D measured, P phenotypic)
curl -s https://chembl.heissa.de/compounds/ivermectin/candidates

# target -> compounds: gene symbol, UniProt accession, target ChEMBL ID or name
curl -s 'https://chembl.heissa.de/targets/ABCB1/compounds?organism=Homo%20sapiens&limit=50'
curl -s 'https://chembl.heissa.de/targets/CHEMBL240/candidates?levels=AB&named=true'

# all SARS-CoV-2 targets, then the compounds measured on the virus
curl -s 'https://chembl.heissa.de/targets/search?q=%25&organism=Severe%20acute%20respiratory%20syndrome%20coronavirus%202'
curl -s 'https://chembl.heissa.de/targets/CHEMBL4303835/compounds?min_phase=4'

# disease -> every compound that reached a clinical trial
curl -s https://chembl.heissa.de/indications/COVID-19

# find names
curl -s 'https://chembl.heissa.de/compounds/search?q=iverm'
curl -s 'https://chembl.heissa.de/targets/search?q=KCNH&organism=Homo%20sapiens'
```

In Python:

```python
import requests
r = requests.get("https://chembl.heissa.de/compounds/nirmatrelvir/candidates", params={"levels": "ABC"})
for t in r.json()["candidates"]:
    print(t["evidence"], t["target_chembl_id"], t["target"], t["organism"], t["best_pchembl"])
```

* Up to 10 000 rows per request; lists with more rows page with `limit` and `offset`
  (the response carries `total`).
* 120 requests per minute and client. Above that the API answers `429` with `Retry-After: 60`.
* Answers are gzip-compressed if the client accepts it, and cached for a day (data only changes with a
  new ChEMBL release).
* Unknown names give `404` with a list of candidates.

All endpoints are listed in [Endpoints](#endpoints) below and documented in Swagger.

## Endpoints

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
| `GET /indications/terms?q=` | disease terms (EFO/MeSH) matching a regular expression, with compound counts |
| `GET /indications/{indication_id}/trials` | the ClinicalTrials.gov trials of one indication with start, primary completion (outcome measured), completion and results-posted dates |
| `GET /indications/{disease}` | all compounds that reached a trial for a disease; `{disease}` is a regular expression over EFO term and MeSH heading, case-insensitive (`lung cancer`, `^non-small cell`, `lung\|bronch`); common German disease words (`Darmkrebs`, `Schlaganfall`) are translated first, and the input is also matched against German names from Wikidata (`chembl_terms_de.py`) |
| `POST /jobs` | queue a batch job, see below |
| `GET /jobs/{job_id}` | job status |
| `GET /jobs/{job_id}/result` | download the job result (xlsx, or zip for csv/jsonl) |
| `GET /version` | ChEMBL release |

Example timings on a desktop server (ChEMBL 37): target → 12 000 compounds 0.7 s, evidence queries
0.01–0.1 s, `/indications/COVID-19` 0.1 s. Responses are gzip-compressed when the client accepts it.

## Large queries: batch jobs via the API

In the [query form](https://chembl.heissa.de/) open *Large job* at the bottom of the page. Through the API:

The interactive API returns at most 10 000 rows per request and allows 120 requests per minute. For
"monster" queries (hundreds of targets or compounds, millions of rows) put a job into the queue instead.
This is an option for large jobs only; everything else is answered ad hoc. The server then

1. runs one endpoint for every name in your list, one name after another,
2. pages through every result on its own (no 10 000 limit, up to 5 million rows per job),
3. writes one file that opens on a plain Windows PC, with an extra first column `query` (the name the
   row belongs to):
   * `xlsx` (default): opens in Excel by double click, numbers stay numbers. Sheets `info` (query,
     parameters, ChEMBL release, legend of the evidence levels), `results` (continued in `results 2`, …
     above 1 048 575 rows) and `errors` (names that could not be resolved).
   * `csv`: zip with the CSV (UTF-8 with BOM, comma separated), `info.txt` and `errors.txt`.
   * `jsonl`: zip with JSON Lines, `info.txt` and `errors.txt`.
4. if you gave an `email` (optional), sends a mail from `chembl@heissa.de` with the download link.
   Without `email` you poll the status and download the file yourself.

Jobs run one after another. Results are kept for 7 days. The `job_id` is the only key to the result,
so keep it private.

### Endpoints a job can run

| `endpoint` | `names` are | one row per |
|---|---|---|
| `/compounds/{name}/targets` | compounds | measured target |
| `/compounds/{name}/candidates` | compounds | target with evidence level |
| `/compounds/{name}/mechanisms` | compounds | curated mechanism |
| `/compounds/{name}/activities` | compounds (needs `params.target`) | single activity record |
| `/compounds/{name}/indications` | compounds | indication |
| `/targets/{name}/compounds` | targets | measured compound |
| `/targets/{name}/candidates` | targets | compound with evidence level |
| `/targets/{name}/mechanisms` | targets | curated drug |
| `/indications/{name}` | diseases | compound in a clinical trial |

`params` takes the query parameters of the endpoint as listed in [Swagger](https://chembl.heissa.de/docs),
e.g. `{"levels": "ABCD"}`, `{"organism": "Homo sapiens", "min_phase": 4}`. `limit` and `offset` are
ignored, the worker pages by itself.

### 1. Submit

```
curl -s -X POST https://chembl.heissa.de/jobs \
  -H 'Content-Type: application/json' \
  -d '{
        "endpoint": "/targets/{name}/candidates",
        "names":    ["CHEMBL4523582", "CHEMBL4303835", "CHEMBL4662936"],
        "params":   {"levels": "ABCDP"},
        "format":   "xlsx"
      }'
# optional: "email": "you@example.org"  -> download link by mail when the job is done
```

```
{"job_id": "cedff819012bbb131421f3d9f1014bf7", "status": "queued", "queue_position": 1,
 "status_url": "/jobs/cedff819012bbb131421f3d9f1014bf7", "note": "keep the job_id: …"}
```

### 2. Poll the status (or wait for the mail)

```
curl -s https://chembl.heissa.de/jobs/cedff819012bbb131421f3d9f1014bf7
```

`status` goes `queued` → `running` (with `n_done` of `n_names` and `n_rows` so far) → `done` or `failed`.

### 3. Download

```
curl -s -OJ https://chembl.heissa.de/jobs/cedff819012bbb131421f3d9f1014bf7/result
# -> chembl_job_cedff819.xlsx   (or .csv.zip / .jsonl.zip)
```

Or simply open the link in the browser: it downloads the file, which opens in Excel.

### The same in Python

```python
import time, requests

base = "https://chembl.heissa.de"
names = [l.strip() for l in open("targets.txt") if l.strip()]   # e.g. 500 target ChEMBL IDs or gene symbols

job = requests.post(f"{base}/jobs", json={
    "endpoint": "/targets/{name}/candidates",
    "names": names,
    "params": {"levels": "ABC", "named": True},
    "format": "jsonl",              # "email": "you@example.org" is optional
}).json()

while (s := requests.get(f"{base}/jobs/{job['job_id']}").json())["status"] in ("queued", "running"):
    print(s["status"], s.get("n_done"), "/", s["n_names"], "names,", s.get("n_rows"), "rows")
    time.sleep(30)

if s["status"] == "done":
    open("result.jsonl.zip", "wb").write(requests.get(base + s["result_url"]).content)
```

### Limits

| | default | environment variable |
|---|---|---|
| names per job | 5 000 | `CHEMBL_JOB_MAX_NAMES` |
| rows per job | 5 000 000 | `CHEMBL_JOB_MAX_ROWS` |
| jobs per client address and 24 h | 20 | `CHEMBL_JOB_PER_IP_DAY` |
| jobs per mail address and 24 h (if given) | 20 | `CHEMBL_JOB_PER_EMAIL_DAY` |
| queued jobs in total | 100 | `CHEMBL_JOB_MAX_QUEUED` |
| result kept | 7 days | `CHEMBL_JOB_KEEP_DAYS` |
| SQL time per statement in a job | 900 s | `CHEMBL_JOB_STATEMENT_TIME` |

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

## Repository

* `chembl.py`: all SQL, plus a CLI.
* `chembl_api.py`: REST API on top of `chembl.py`, Swagger UI at `/docs`.
* `chembl_mech_build.py`: builds the table `x_mech_candidates` and the view `x_mech_evidence` (evidence layer).
* `chembl_jobs.py`: batch job worker for large queries (result file, optional mail).
* `chembl_trials.py`: loads status and dates of the ~90 000 trials behind `drug_indication` from the ClinicalTrials.gov API (`ct_trial`, `ct_indication`, summary columns `trial_*` in `drug_indication`); run it after each ChEMBL import and now and then to refresh.
* `chembl_terms_de.py`: adds German disease names and synonyms from Wikidata (CC0) to `drug_indication` (`efo_term_de`, `efo_synonyms_de`, `mesh_heading_de`, `mesh_synonyms_de`) for the German disease search; run it again after each ChEMBL import.
* `web/`: query form (PHP + Bootstrap, local assets).
* `chembl-api.service`, `chembl-jobs.service`: example systemd units.

Tested with ChEMBL 37 on MariaDB 11.8. ChEMBL data itself is not part of this repository.

## Setup

```
pip install --user -r requirements.txt          # pymysql, fastapi, uvicorn, httpx (redis optional)
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

## Running your own public instance

* `web/` is the query form: point the web server's document root at it (PHP ≥ 8.1 with curl). The API base
  URL comes from `SetEnv CHEMBL_API …` (mod_env) or from `/etc/chembl/web.ini` (`api = "http://host:9099"`).
  Bootstrap 5.3.3 is shipped in `web/assets/` (checked against the official SRI hashes), so visitors load
  nothing from third parties.
* Proxy only the API paths (`/docs`, `/openapi.json`, `/version`, `/compounds`, `/targets`, `/indications`,
  `/jobs`) to the API.
* fail2ban on the proxy: count every request per address in the access log (cached answers never reach the
  API rate limit) and ban clients that keep receiving 429. Example filters: `failregex = ^<HOST> -[^"]*"(?:GET|HEAD|POST|OPTIONS) `
  (ignore `GET /assets/`), and `failregex = ^<HOST> -[^"]*"[^"]*" 429 `.

The public instance is a reverse proxy (Apache, TLS, disk cache) in front of the API, which runs next to
the database on another machine, reached **directly over IPv6** (`ProxyPass /… http://<api-host>:9099/…`). Only
results take that path. Until 2026-11-10 it went through a WireGuard tunnel to a VPS; that VPS was given up, so
the proxy now addresses the API host by its own AAAA record. The API still decides for itself who may ask:
`CHEMBL_ALLOW` holds the networks allowed to connect at all and `CHEMBL_TRUSTED_PROXIES` the proxy addresses
whose `X-Forwarded-For` counts.

| variable | purpose |
|---|---|
| `CHEMBL_ALLOW` | networks allowed to connect to the API at all (empty = everyone) |
| `CHEMBL_TRUSTED_PROXIES` | reverse proxies whose `X-Forwarded-For` is used as client address |
| `CHEMBL_RATE_PER_MIN` | requests per minute and client behind a trusted proxy (default 120, shared across workers through redis if available) |
| `CHEMBL_MAX_STATEMENT_TIME` | SQL time per statement for interactive requests (default 30 s) |
| `CHEMBL_CACHE_MAX_AGE` | `Cache-Control: max-age` of successful GET responses (default 86 400 s) |
| `CHEMBL_JOB_DIR`, `CHEMBL_PUBLIC_URL`, `CHEMBL_MAIL_FROM`, `CHEMBL_SMTP_HOST` | batch jobs: result directory, base URL in mails, sender, SMTP relay |

Apache needs `CacheIgnoreNoLastMod On` (the API sends no `Last-Modified`) and its own `CacheRoot` in the
virtual host, otherwise nothing is cached.

## Roadmap

1. **Stage 1 (done):** evidence from ChEMBL `activities`, levels A–D and P.
2. **Stage 2:** further open curated sources in the same table with their own `source`:
   DrugCentral, IUPHAR/BPS Guide to Pharmacology, BindingDB.
3. **Stage 3:** literature and clinical outcome, as a separate axis: PubTator3 chemical–gene relations
   (text-mined, own level), ClinicalTrials.gov results, living meta-analyses such as COVID-NMA.

## Licence

Code: MIT, see [LICENSE](LICENSE). Bootstrap: MIT.

Data: ChEMBL is published by EMBL-EBI under CC BY-SA 3.0. Cite: Zdrazil et al., *The ChEMBL Database in 2023*,
Nucleic Acids Res. 52 (2024) D1180–D1192.
