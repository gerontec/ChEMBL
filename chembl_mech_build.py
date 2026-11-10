#!/usr/bin/env python3
"""Build x_mech_candidates + view x_mech_evidence in the local ChEMBL schema (stage 1: derived from activities).

x_mech_candidates  raw aggregates per (compound, target) over all activities with a pChEMBL value
                   (ChEMBL leaves pchembl_value NULL for records flagged by data_validity_comment).
x_mech_evidence    view: evidence level per pair + curated drug_mechanism pairs (level A).

Levels (strongest first; a curated pair is A even if it also has measurements):
  A  curated in drug_mechanism
  B  molecular target, assay confidence >= 8, best pChEMBL >= 7, active (pChEMBL >= 5) in >= 2 documents
  C  molecular target, assay confidence >= 8, best pChEMBL >= 6
  D  molecular target, best pChEMBL >= 5 (about 10 uM)
  P  phenotypic target (organism, cell line, tissue, ...), best pChEMBL >= 5
Pairs with best pChEMBL < 5 are stored too (level '-') so nothing measured is lost.

Rebuild after each ChEMBL import:  ./chembl_mech_build.py
The table is built as x_mech_candidates_new and swapped in atomically with RENAME TABLE.
"""

import sys
import time

import chembl

MOLECULAR = ("SINGLE PROTEIN", "PROTEIN-PROTEIN INTERACTION", "PROTEIN COMPLEX", "PROTEIN FAMILY",
             "PROTEIN COMPLEX GROUP", "SELECTIVITY GROUP", "CHIMERIC PROTEIN", "PROTEIN NUCLEIC-ACID COMPLEX",
             "NUCLEIC-ACID", "SMALL MOLECULE", "OLIGOSACCHARIDE", "MACROMOLECULE", "LIPID", "METAL")
PHENOTYPIC = ("ORGANISM", "CELL-LINE", "TISSUE", "SUBCELLULAR", "3D CELL CULTURE", "PHENOTYPE")
# not used: UNKNOWN, NON-MOLECULAR, UNCHECKED, ADMET, NO TARGET


def sql_list(values):
    return ",".join("'" + v.replace("'", "''") + "'" for v in values)


DDL = """
CREATE TABLE x_mech_candidates_new (
    molregno       BIGINT       NOT NULL,
    tid            BIGINT       NOT NULL,
    target_class   ENUM('molecular','phenotypic') NOT NULL,
    n_meas         INT          NOT NULL COMMENT 'activities with pChEMBL',
    n_active       INT          NOT NULL COMMENT 'activities with pChEMBL >= 5',
    n_docs         INT          NOT NULL COMMENT 'documents with any pChEMBL',
    n_docs_active  INT          NOT NULL COMMENT 'documents with pChEMBL >= 5',
    best_pchembl   DECIMAL(4,2) NOT NULL,
    mean_pchembl   DECIMAL(4,2) NOT NULL,
    max_conf       TINYINT      NULL     COMMENT 'max assay confidence_score',
    types          VARCHAR(500) NULL     COMMENT 'standard_type list',
    first_year     SMALLINT     NULL,
    last_year      SMALLINT     NULL,
    PRIMARY KEY (molregno, tid),
    KEY idx_xmc_tid (tid, best_pchembl)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
  COMMENT='derived compound-target evidence from activities, built by chembl_mech_build.py'
"""

FILL = f"""
INSERT INTO x_mech_candidates_new
SELECT a.molregno, s.tid,
       IF(td.target_type IN ({sql_list(MOLECULAR)}), 'molecular', 'phenotypic'),
       COUNT(*),
       SUM(a.pchembl_value >= 5),
       COUNT(DISTINCT a.doc_id),
       COUNT(DISTINCT IF(a.pchembl_value >= 5, a.doc_id, NULL)),
       MAX(a.pchembl_value),
       ROUND(AVG(a.pchembl_value), 2),
       MAX(s.confidence_score),
       LEFT(GROUP_CONCAT(DISTINCT a.standard_type ORDER BY a.standard_type SEPARATOR ','), 500),
       MIN(doc.year),
       MAX(doc.year)
FROM activities a
JOIN assays s ON s.assay_id = a.assay_id
JOIN target_dictionary td ON td.tid = s.tid
LEFT JOIN docs doc ON doc.doc_id = a.doc_id
WHERE a.pchembl_value IS NOT NULL
  AND td.target_type IN ({sql_list(MOLECULAR + PHENOTYPIC)})
GROUP BY a.molregno, s.tid
"""

VIEW = """
CREATE OR REPLACE VIEW x_mech_evidence AS
SELECT c.molregno, c.tid,
       CASE
         WHEN m.molregno IS NOT NULL THEN 'A'
         WHEN c.target_class = 'phenotypic' AND c.best_pchembl >= 5 THEN 'P'
         WHEN c.target_class = 'phenotypic' THEN '-'
         WHEN c.max_conf >= 8 AND c.best_pchembl >= 7 AND c.n_docs_active >= 2 THEN 'B'
         WHEN c.max_conf >= 8 AND c.best_pchembl >= 6 THEN 'C'
         WHEN c.best_pchembl >= 5 THEN 'D'
         ELSE '-'
       END AS evidence,
       'chembl_activities' AS source,
       c.target_class, c.n_meas, c.n_active, c.n_docs, c.n_docs_active, c.best_pchembl, c.mean_pchembl,
       c.max_conf, c.types, c.first_year, c.last_year,
       m.mechanism_of_action, m.action_type
FROM x_mech_candidates c
LEFT JOIN (SELECT molregno, tid,
                  GROUP_CONCAT(DISTINCT mechanism_of_action SEPARATOR '; ') AS mechanism_of_action,
                  GROUP_CONCAT(DISTINCT action_type SEPARATOR ',') AS action_type
           FROM drug_mechanism WHERE tid IS NOT NULL GROUP BY molregno, tid) m
       ON m.molregno = c.molregno AND m.tid = c.tid
UNION ALL
SELECT dm.molregno, dm.tid, 'A', 'drug_mechanism',
       NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
       GROUP_CONCAT(DISTINCT dm.mechanism_of_action SEPARATOR '; '),
       GROUP_CONCAT(DISTINCT dm.action_type SEPARATOR ',')
FROM drug_mechanism dm
LEFT JOIN x_mech_candidates c ON c.molregno = dm.molregno AND c.tid = dm.tid
WHERE dm.tid IS NOT NULL AND c.molregno IS NULL
GROUP BY dm.molregno, dm.tid
"""


def main():
    db = chembl.connect(read_timeout=3600)
    cur = db.cursor()
    cur.execute("SET SESSION character_set_collations='utf8mb3=utf8mb3_general_ci,utf8mb4=utf8mb4_general_ci'")
    cur.execute("DROP TABLE IF EXISTS x_mech_candidates_new")
    cur.execute(DDL)
    t = time.time()
    print("filling x_mech_candidates_new ...", flush=True)
    cur.execute(FILL)
    db.commit()
    print(f"  {cur.rowcount} pairs in {time.time() - t:.0f} s", flush=True)

    cur.execute("SHOW TABLES LIKE 'x_mech_candidates'")
    if cur.fetchone():
        cur.execute("DROP TABLE IF EXISTS x_mech_candidates_old")
        cur.execute("RENAME TABLE x_mech_candidates TO x_mech_candidates_old, "
                    "x_mech_candidates_new TO x_mech_candidates")
        cur.execute("DROP TABLE x_mech_candidates_old")
    else:
        cur.execute("RENAME TABLE x_mech_candidates_new TO x_mech_candidates")
    cur.execute(VIEW)
    db.commit()

    t = time.time()
    rows = chembl.q(cur, "SELECT evidence, COUNT(*) AS pairs, COUNT(DISTINCT molregno) AS compounds "
                         "FROM x_mech_evidence GROUP BY evidence ORDER BY evidence")
    print(f"evidence levels ({time.time() - t:.0f} s):")
    for r in rows:
        print(f"  {r['evidence']}  pairs {r['pairs']:>9}  compounds {r['compounds']:>8}")


if __name__ == "__main__":
    sys.exit(main())
