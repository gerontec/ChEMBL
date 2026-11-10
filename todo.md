# To do

Started on 2026-11-10 and not finished. Companion list in the nr repository:
`github.com/gerontec/blastDiamond/todo.md` (the two services are joined by `nr_join.py`).

## 1. The ChEMBL ↔ nr joins are in service (2026-11-10)

Done and checked through the public URL: `CHEMBL_BLAST_URL=https://blast.heissa.de` in `/etc/chembl.env`,
`chembl-api` restarted, `ProxyPass /nr` in the heissa vhost, `git pull` in `/opt/ChEMBL` for `nr.php`.
`https://chembl.heissa.de/nr/compounds/scopolamine` answers with 42 targets, and `/openapi.json` lists the four
`/nr/...` paths (21 paths in all), so Swagger and ReDoc describe them without any extra step.

Two things that cost time and are worth knowing next time:

- the Apache disk cache keeps `openapi.json` for a day (`Cache-Control: max-age=86400`), so the new paths were
  invisible after the restart. The cache key is the **public** URL, not the backend one:
  `sudo htcacheclean -v -p /var/cache/apache2/mod_cache_disk "https://chembl.heissa.de:443/openapi.json?"`
- the proxy no longer goes through the WireGuard tunnel of the cancelled VPS but straight to the API host over
  IPv6; see README, section on the public instance

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

## 3. Target prediction: trial 2 is measured, the baseline won

Done on 2026-11-10 (`dti/CONCEPT.md`, section Trial 2, and `dti/mt_test.py`): the multitask design with actives
and inactives was trained on our ChEMBL 37 (643 human targets, 1.77 M labels, 1.0 M compounds, 34 s per epoch on
the P4) and measured with the same scopolamine blind test. It reaches AUC 0.909 / MCC 0.660 per pair on compounds
it has never seen, and it fails the blind test: the five muscarinic receptors land at ranks 83–142 of 643, while
the Tanimoto similarity baseline puts them at 1, 3, 6, 7, 8. Criteria 2 and 3 missed, the trial counts as failed.

- [ ] put the **Tanimoto target fishing** into the form and the API, since that is what passed: for a pasted
      SMILES, the highest similarity to each target's actives, shown with the similarity and the compound it came
      from, separated from measured data and never inside `x_mech_evidence`
- [ ] it costs about 0.15 s per compound against the 906 721 reference fingerprints on one CPU thread; decide
      whether that runs per query (needed anyway for compounds that are not in ChEMBL) or whether the 1.0 M
      ChEMBL compounds get a precomputed table as a cache
- [ ] the mapping covers **643 of the 12 912 target accessions** ChEMBL knows: only targets with at least 100
      active and 100 inactive compounds. Say so in the form, it is not a mapping of all human proteins
- [ ] only if a next trial beats the baseline on the same blind test: a ranking loss across the targets of one
      compound, or a per-compound calibration — the per-target heads are each calibrated to their own target,
      which is why the ranking across targets fails

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
