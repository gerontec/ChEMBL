#!/usr/bin/env python3
"""Fill x_uniprot_refseq: RefSeq protein accessions of the UniProt accessions ChEMBL uses as target components.

ChEMBL names its target proteins by UniProt accession. nr carries Swiss-Prot entries under that same accession
(P02766.1), but TrEMBL accessions are not in nr at all, and the same protein is in nr as a RefSeq entry
(NP_/XP_/WP_) under a different accession. This table is the bridge, so nr_join finds an nr entry for a target
whose UniProt accession itself is missing from nr.

Source: ftp.ncbi.nlm.nih.gov/gene/DATA/gene_refseq_uniprotkb_collab.gz (NCBI + UniProt, two columns
'NCBI_protein_accession<TAB>UniProtKB_protein_accession'). Only the rows of ChEMBL's own accessions are kept
(about 13 000 of them), so the table stays small instead of holding the whole file.

  python3 nr_uniprot_map.py --file /mnt/archive/nr_map/gene_refseq_uniprotkb_collab.gz
  python3 nr_uniprot_map.py --file ... --check      only count, write nothing
"""
import argparse
import gzip
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chembl

T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:6.1f}s] {msg}", flush=True)


DDL = """CREATE TABLE IF NOT EXISTS x_uniprot_refseq (
  uniprot VARCHAR(16) NOT NULL COMMENT 'component_sequences.accession',
  refseq VARCHAR(32) NOT NULL COMMENT 'NCBI protein accession, no version',
  PRIMARY KEY (uniprot, refseq),
  KEY idx_refseq (refseq)
) DEFAULT CHARSET=ascii COMMENT='RefSeq accessions per ChEMBL target UniProt accession (NCBI gene_refseq_uniprotkb_collab)'"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    con = chembl.connect(read_timeout=3600)
    con.autocommit(True)
    c = con.cursor()
    c.execute("SELECT accession FROM component_sequences WHERE accession IS NOT NULL AND component_type = 'PROTEIN'")
    want = {r["accession"] for r in c.fetchall()}
    log(f"{len(want)} UniProt accessions of ChEMBL target components")

    rows, seen_lines, header = [], 0, None
    with gzip.open(a.file, "rt", encoding="ascii", errors="replace") as f:
        for line in f:
            seen_lines += 1
            if header is None:                      # '#NCBI_protein_accession\tUniProtKB_protein_accession'
                header = line.rstrip("\n")
                if header.startswith("#"):
                    continue
            p = line.rstrip("\n").split("\t")
            if len(p) < 2:
                continue
            uni = p[1].split(".")[0]
            if uni in want:
                rows.append((uni, p[0].split(".")[0]))
    log(f"{seen_lines} lines read, {len(rows)} rows for ChEMBL accessions, "
        f"{len({u for u, _ in rows})} of {len(want)} accessions covered (header: {header})")
    if a.check:
        return
    c.execute(DDL)
    c.execute("DELETE FROM x_uniprot_refseq")
    for i in range(0, len(rows), 5000):
        c.executemany("INSERT IGNORE INTO x_uniprot_refseq (uniprot, refseq) VALUES (%s,%s)", rows[i:i + 5000])
    c.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT uniprot) AS u FROM x_uniprot_refseq")
    log(f"x_uniprot_refseq: {c.fetchone()}")


if __name__ == "__main__":
    main()
