# To do

Started on 2026-11-10 and not finished. Companion list in the nr repository:
`github.com/gerontec/blastDiamond/todo.md` (the two services are joined by `nr_join.py`).

## 1. Put the ChEMBL ↔ nr joins into service

The code is committed and tested against a local test instance, but the public service still runs without it.

- [ ] `CHEMBL_BLAST_URL=https://blast.heissa.de` into `/etc/chembl.env`, then
      `systemctl restart chembl-api` — without it the `/nr/...` answers carry no DIAMOND links
- [ ] `ProxyPass /nr http://<api>:9099/nr` in the heissa vhost (the other paths are listed one by one there)
- [ ] deploy `web/nr.php` and `web/index.php` to heissa (`git pull` in `/opt/ChEMBL`)
- [ ] check the form through the public URL (the test instance ran on the database server, where PHP had no curl
      extension until php-curl was installed on 2026-11-10)

## 2. UniProt → RefSeq bridge (`x_uniprot_refseq`)

So that a target whose UniProt accession is not in nr (TrEMBL: 1 318 of ChEMBL's components) still finds its nr
entry. `nr_uniprot_map.py` fills the table from NCBI `gene_refseq_uniprotkb_collab.gz` (791 MB, 2026-11-05),
`nr_join.refseq_of()` uses it and reports the route in `via`.

Loaded 2026-11-10: 125 284 878 lines read, 44 015 rows kept, 12 075 of 12 912 ChEMBL accessions covered.
Tested on 40 TrEMBL accessions: none is in nr directly, 27 were found through their RefSeq entry (before the
bridge all 40 were missing).

- [ ] **the source file has five columns**, the fifth is `method` (`identical` and others). The loader keeps every
      row regardless of method; decide whether to keep only `identical` and reload
- [ ] count how many of the 12 912 target accessions resolve to an nr entry now against before the bridge
- [ ] the file is not refreshed anywhere: either a cron entry or a note that the table ages

## 3. Next field trial: ChEMBL's own multitask model

Our own model failed (`dti/CONCEPT.md`, run 1). The ChEMBL multitask target-prediction model
(`github.com/chembl/chembl_multitask_model`, MIT) trains **with inactive compounds** (at least 100 active and 100
inactive per target), which is exactly what our run lacked.

- [ ] train it on the local ChEMBL 37 (its scripts document ChEMBL 36) and measure it with the **same blind test**
      (scopolamine, ranks of the five muscarinic receptors among all human targets)
- [ ] write the pass/fail criteria into a new section of `dti/CONCEPT.md` **before** the first result
- [ ] second baseline: nearest neighbours by Tanimoto among the measured ligands per target
- [ ] only if it passes: show predictions in the form and the API, separated from measured data
      (`predicted_pchembl`), never inside the evidence layer `x_mech_evidence`

## 4. Further sources for toxins (checked 2026-11-10)

- [ ] **CTD** `CTD_chem_gene_ixns.tsv.gz` (44 MB, 2026-10-29) — curated chemical ↔ gene, covers toxins where
      ChEMBL has gaps. **Read the terms of use first** (`ctdbase.org/about/legal.jsp`), they are not a standard licence
- [ ] **T3DB** (toxins and their human targets) — free for non-commercial use, citation requested. Its server
      answers automated requests with HTTP 403; the file has to be fetched from `t3db.ca/downloads` by hand
- [ ] **PubChem BioAssay** names its targets by NCBI protein accession, which fits nr directly, but no ready-made
      mapping table was found on the FTP site — would have to be built from the bulk files
- STITCH was dropped: newest version found is from 2016

## 5. Known limit, no fix planned

Reverse joins by identical sequence find nothing for the proteins of a toxic plant or animal itself: ChEMBL lists
the proteins a compound acts **on**, not the ones that make it (`example/datura_stramonium.md`). The way back is a
DIAMOND homolog search, which the form offers per protein.
