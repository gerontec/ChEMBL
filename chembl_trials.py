#!/usr/bin/env python3
"""Dates and status of the clinical trials behind drug_indication, from ClinicalTrials.gov.

ChEMBL links a drug indication to its trials (indication_refs, ref_type 'ClinicalTrials', a comma list of
NCT numbers) but stores no dates or outcomes. This script loads them from the ClinicalTrials.gov API v2:

  ct_trial          one row per NCT number WITH POSTED RESULTS (final report on ClinicalTrials.gov);
                    trials without results are not stored and are checked again on the next run: title, status, phase, enrollment, start date,
                    primary completion date (the last participant was examined for the primary outcome,
                    i.e. when the main result was measured; ACTUAL or ESTIMATED), completion date,
                    first posting of results, has_results, why_stopped (reason given for a stop),
                    allocation (RANDOMIZED ...), masking (NONE/SINGLE/DOUBLE/...), controlled (comparator arm),
                    biomarker_kind ('DNA' = inclusion requires a DNA/RNA tumour test such as KRAS G12C or MSI-H,
                    'expression' = a protein marker such as PD-L1), biomarker_text (that criterion)
  ct_trial_marker   nct_id, marker, kind: the markers found in the inclusion criteria
  ct_indication     drugind_id <-> nct_id, all trials ChEMBL links (with or without results)
  drug_indication   summary columns per indication: trial_n (all linked trials), trial_n_results,
                    trial_first_start, trial_last_primary_completion (ACTUAL dates only),
                    trial_last_results_posted (the dates of trials with results)

Run it after each ChEMBL import and now and then to refresh the trial status:
    python3 chembl_trials.py                    # all trials
    python3 chembl_trials.py --open-only        # only NCT numbers not stored yet, i.e. still without results (cron)
    python3 chembl_trials.py --summary-only     # recompute the drug_indication columns
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import chembl

API = "https://clinicaltrials.gov/api/v2/studies"
AGENT = "chembl-trials/1.0 (https://github.com/gerontec/ChEMBL)"
BATCH = 500
FIELDS = ("NCTId,BriefTitle,OverallStatus,Phase,EnrollmentCount,StartDate,PrimaryCompletionDate,"
          "PrimaryCompletionDateType,CompletionDate,ResultsFirstPostDate,HasResults,WhyStopped,"
          "EligibilityCriteria,DesignAllocation,DesignMasking,ArmGroupType")

DDL = [
    """CREATE TABLE IF NOT EXISTS ct_trial (
        nct_id                  VARCHAR(11) NOT NULL PRIMARY KEY,
        title                   VARCHAR(600),
        status                  VARCHAR(40)  COMMENT 'COMPLETED, TERMINATED, RECRUITING, ...',
        why_stopped             VARCHAR(500) COMMENT 'reason given for TERMINATED/SUSPENDED/WITHDRAWN',
        phase                   VARCHAR(40)  COMMENT 'e.g. PHASE2/PHASE3',
        enrollment              INT,
        start_date              DATE,
        primary_completion_date DATE         COMMENT 'primary outcome measured on the last participant',
        primary_completion_type VARCHAR(10)  COMMENT 'ACTUAL or ESTIMATED',
        completion_date         DATE,
        results_first_posted    DATE,
        has_results             TINYINT(1),
        updated                 TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci COMMENT='ClinicalTrials.gov dates (chembl_trials.py)'""",
    """CREATE TABLE IF NOT EXISTS ct_trial_marker (
        nct_id VARCHAR(11) NOT NULL,
        marker VARCHAR(60) NOT NULL COMMENT 'e.g. KRAS G12C, MSI-H/dMMR, PD-L1',
        kind   VARCHAR(12) NOT NULL COMMENT 'DNA or expression',
        PRIMARY KEY (nct_id, marker), KEY (marker)
    ) DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci COMMENT='tumour markers required by the inclusion criteria'""",
    """CREATE TABLE IF NOT EXISTS ct_indication (
        drugind_id BIGINT NOT NULL,
        nct_id     VARCHAR(11) NOT NULL,
        PRIMARY KEY (drugind_id, nct_id), KEY (nct_id)
    ) DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci COMMENT='drug_indication <-> NCT (chembl_trials.py)'""",
    """ALTER TABLE drug_indication
        ADD COLUMN IF NOT EXISTS trial_n INT COMMENT 'ClinicalTrials.gov trials for this indication',
        ADD COLUMN IF NOT EXISTS trial_n_results INT COMMENT 'of them with posted results',
        ADD COLUMN IF NOT EXISTS trial_first_start DATE COMMENT 'earliest trial start',
        ADD COLUMN IF NOT EXISTS trial_last_primary_completion DATE COMMENT 'latest ACTUAL primary completion date',
        ADD COLUMN IF NOT EXISTS trial_last_results_posted DATE COMMENT 'latest first posting of results',
        ADD COLUMN IF NOT EXISTS trial_n_dna INT COMMENT 'trials with results whose inclusion requires a DNA tumour test',
        ADD COLUMN IF NOT EXISTS trial_n_biomarker INT COMMENT 'trials with results selecting by any tumour marker',
        ADD COLUMN IF NOT EXISTS trial_n_randomized INT COMMENT 'trials with results that are randomized',
        ADD COLUMN IF NOT EXISTS trial_markers VARCHAR(500) COMMENT 'tumour markers of these trials'""",
    """ALTER TABLE ct_trial
        ADD COLUMN IF NOT EXISTS allocation VARCHAR(20) COMMENT 'RANDOMIZED, NON_RANDOMIZED, NA',
        ADD COLUMN IF NOT EXISTS masking VARCHAR(20) COMMENT 'NONE, SINGLE, DOUBLE, TRIPLE, QUADRUPLE',
        ADD COLUMN IF NOT EXISTS controlled TINYINT(1) COMMENT 'has a placebo/active/sham comparator arm',
        ADD COLUMN IF NOT EXISTS biomarker_kind VARCHAR(12) COMMENT 'DNA, expression or NULL',
        ADD COLUMN IF NOT EXISTS biomarker_text VARCHAR(500) COMMENT 'inclusion criterion asking for the marker'""",
]


def day(s):
    """'2014-05' -> '2014-05-01', '2014' -> '2014-01-01', None stays None."""
    if not s:
        return None
    parts = s.split("-")
    return "-".join(parts + ["01"] * (3 - len(parts)))


def fetch(ids):
    """ClinicalTrials.gov records for one batch of NCT numbers (missing/withdrawn ones are simply absent)."""
    params = {"filter.ids": ",".join(ids), "fields": FIELDS, "pageSize": 1000}
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": AGENT, "Accept": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r).get("studies", [])
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))


# ------------------------------------------------------------------ biomarker selection in the inclusion criteria
# A trial is "biomarker-selected" when an inclusion criterion asks for a tumour marker. kind 'DNA' = the marker is
# found by a DNA/RNA test (mutation, wild type, fusion, amplification, MSI/dMMR, TMB, HRD, sequencing, ctDNA);
# 'expression' = protein expression (IHC, PD-L1 score, HER2 3+, CD20 ...). Gene symbols are matched case-sensitively.
GENES = (r"KRAS|NRAS|HRAS|RAS|BRAF|EGFR|ALK|ROS-?1|NTRK[123]?|RET|MET|HER-?2|ERBB[23]|BRCA[12]?|PIK3CA|PTEN|AKT1|"
         r"IDH[12]?|FGFR[1-4]?|KIT|PDGFRA|FLT-?3|NPM1|JAK2|BCR-?ABL1?|ESR1|ATM|PALB2|CDK12|TP53|KMT2A|NRG1|"
         r"MAP2K1|NF1|STK11|KEAP1|POLE|EZH2|SMARCB1|CLDN18(?:\.2)?|PD-?L1|TROP-?2|FOLR1|DLL3|CD19|CD20|CD22|"
         r"CD30|CD33|CD38|CD123|BCMA|GPRC5D|PSMA|SSTR2?|AR-V7")
_GENE = re.compile(r"(?<![A-Za-z0-9-])(" + GENES + r")(?![A-Za-z0-9])")
_DNA_ONLY = re.compile(r"\bMSI-?H\b|\bMSI\b|microsatellite instab|\bdMMR\b|\bpMMR\b|mismatch repair|"
                       r"tumou?r mutation(al)? burden|\bTMB\b|\bHRD\b|homologous recombination|\bctDNA\b|"
                       r"\bNGS\b|next[- ]generation sequencing|genomic (alteration|profil|test)", re.I)
_DNA_Q = re.compile(r"mutat|mutant|wild[- ]?type|\bwt\b|fusion|rearrang|translocat|amplif|alteration|variant|"
                    r"deletion|insertion|exon|copy number|\bloss\b|deficien|germline|somatic|sequenc|\bPCR\b|"
                    r"\bFISH\b|\bISH\b|genotyp|[A-Z]\d{2,4}[A-Z]\b|\bITD\b|\bTKD\b|positive by (PCR|FISH|NGS)", re.I)
_EXPR_Q = re.compile(r"express|overexpress|positiv|\bIHC\b|immunohisto|\b[23]\+|\bhigh\b|\bTPS\b|\bCPS\b|"
                     r"[≥>]=? ?\d+ ?%|score", re.I)
_VARIANT = re.compile(r"\b([A-Z]\d{2,4}[A-Z]|exon ?\d{1,2}(?: (?:deletion|insertion)s?)?|ITD|V600[EK]?)\b")
_MSI = re.compile(r"\bMSI-?H\b|microsatellite instab|\bdMMR\b|mismatch repair", re.I)
_EXCL = re.compile(r"\n\s*exclusion criteria\s*:?", re.I)


def classify_biomarkers(eligibility):
    """(markers, kind, text) from the eligibility text: markers = {'KRAS G12C': 'DNA', 'PD-L1': 'expression'},
    kind 'DNA' / 'expression' / None, text = the first inclusion criterion that asks for a marker."""
    if not eligibility:
        return {}, None, None
    inclusion = _EXCL.split(eligibility, 1)[0]
    markers, first = {}, None
    for line in inclusion.split("\n"):
        line = line.strip(" *-\t")
        if len(line) < 6:
            continue
        found = {}
        if _MSI.search(line):
            found["MSI-H/dMMR"] = "DNA"
        if re.search(r"\bTMB\b|tumou?r mutation(al)? burden", line, re.I):
            found["TMB"] = "DNA"
        if re.search(r"\bHRD\b|homologous recombination", line, re.I):
            found["HRD"] = "DNA"
        dna_line = bool(_DNA_Q.search(line)) or bool(_DNA_ONLY.search(line))
        for m in _GENE.finditer(line):
            g = m.group(1).replace("-", "") if m.group(1) not in ("PD-L1", "AR-V7", "BCR-ABL", "BCR-ABL1") else m.group(1)
            g = {"HER2": "HER2", "ERBB2": "HER2", "PDL1": "PD-L1", "ROS1": "ROS1", "TROP2": "TROP2", "FLT3": "FLT3"}.get(g, g)
            v = _VARIANT.search(line, m.end(), m.end() + 40)
            label = f"{g} {v.group(1)}" if v and not v.group(1).startswith(g) else g
            kind = "DNA" if dna_line else ("expression" if _EXPR_Q.search(line) else None)
            if kind:
                found[label] = "DNA" if "DNA" in (kind, found.get(label)) else kind
        if not found and _DNA_ONLY.search(line):
            found["NGS/genomic test"] = "DNA"
        if found:
            first = first or line[:500]
            for k, v in found.items():
                markers[k] = "DNA" if "DNA" in (v, markers.get(k)) else v
    for k in [k for k in markers if " " not in k]:     # "KRAS" is redundant next to "KRAS G12C"
        if any(o.startswith(k + " ") for o in markers):
            del markers[k]
    kinds = set(markers.values())
    return markers, ("DNA" if "DNA" in kinds else ("expression" if kinds else None)), first



def row(st):
    p = st.get("protocolSection", {})
    s = p.get("statusModule", {})
    d = p.get("designModule", {})
    pc = s.get("primaryCompletionDateStruct", {})
    di = d.get("designInfo", {})
    arms = {g.get("type") for g in p.get("armsInterventionsModule", {}).get("armGroups", [])}
    markers, kind, text = classify_biomarkers(p.get("eligibilityModule", {}).get("eligibilityCriteria"))
    return (p.get("identificationModule", {}).get("nctId"),
            (p.get("identificationModule", {}).get("briefTitle") or "")[:600] or None,
            s.get("overallStatus"), (s.get("whyStopped") or "")[:500] or None, "/".join(d.get("phases", []))[:40] or None,
            d.get("enrollmentInfo", {}).get("count"),
            day(s.get("startDateStruct", {}).get("date")), day(pc.get("date")), pc.get("type"),
            day(s.get("completionDateStruct", {}).get("date")),
            day(s.get("resultsFirstPostDateStruct", {}).get("date")), int(bool(st.get("hasResults"))),
            di.get("allocation"), di.get("maskingInfo", {}).get("masking"),
            int(bool(arms & {"PLACEBO_COMPARATOR", "ACTIVE_COMPARATOR", "SHAM_COMPARATOR"})), kind, text), markers


def summarise(con, cur):
    cur.execute("""UPDATE drug_indication di LEFT JOIN (
            SELECT ci.drugind_id, COUNT(*) AS n, COUNT(t.nct_id) AS n_res, MIN(t.start_date) AS first_start,
                   MAX(CASE WHEN t.primary_completion_type = 'ACTUAL' THEN t.primary_completion_date END) AS last_pc,
                   MAX(t.results_first_posted) AS last_res, SUM(t.biomarker_kind = 'DNA') AS n_dna,
                   COUNT(t.biomarker_kind) AS n_bm, SUM(t.allocation = 'RANDOMIZED') AS n_rand,
                   (SELECT SUBSTRING(GROUP_CONCAT(DISTINCT m.marker ORDER BY m.kind, m.marker SEPARATOR ', '), 1, 500)
                    FROM ct_indication c2 JOIN ct_trial_marker m ON m.nct_id = c2.nct_id
                    WHERE c2.drugind_id = ci.drugind_id) AS markers
            FROM ct_indication ci LEFT JOIN ct_trial t ON t.nct_id = ci.nct_id GROUP BY ci.drugind_id) a
            ON a.drugind_id = di.drugind_id
        SET di.trial_n = a.n, di.trial_n_results = a.n_res, di.trial_first_start = a.first_start,
            di.trial_last_primary_completion = a.last_pc, di.trial_last_results_posted = a.last_res,
            di.trial_n_dna = a.n_dna, di.trial_n_biomarker = a.n_bm, di.trial_n_randomized = a.n_rand,
            di.trial_markers = a.markers""")
    con.commit()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refetch-stored", action="store_true",
                    help="fetch only the stored trials again (e.g. after new fields were added)")
    ap.add_argument("--summary-only", action="store_true", help="only recompute the drug_indication columns")
    ap.add_argument("--open-only", action="store_true",
                    help="fetch only NCT numbers not stored yet (trials still without posted results and new ones); "
                         "stored trials are kept as they are")
    a = ap.parse_args()
    con = chembl.connect()
    cur = con.cursor()
    for s in DDL:
        cur.execute(s)
    cur.execute("ALTER TABLE ct_trial ADD COLUMN IF NOT EXISTS why_stopped VARCHAR(500) AFTER status")
    cur.execute("DELETE FROM ct_trial WHERE has_results = 0")    # rows of older runs without results
    cur.execute("DELETE FROM ct_trial_marker WHERE nct_id NOT IN (SELECT nct_id FROM ct_trial)")
    con.commit()
    if not a.summary_only:
        cur.execute("SELECT drugind_id, ref_id FROM indication_refs WHERE ref_type = 'ClinicalTrials'")
        links = {(r["drugind_id"], n.strip()) for r in cur.fetchall() for n in r["ref_id"].split(",")
                 if n.strip().startswith("NCT")}
        cur.execute("DELETE FROM ct_indication")
        cur.executemany("INSERT INTO ct_indication (drugind_id, nct_id) VALUES (%s, %s)", sorted(links))
        con.commit()
        ids = sorted({n for _, n in links})
        if a.open_only:
            cur.execute("SELECT nct_id FROM ct_trial")
            done = {r["nct_id"] for r in cur.fetchall()}
            print(f"{len(ids)} NCT numbers, {len(done)} stored with results skipped", file=sys.stderr)
            ids = [n for n in ids if n not in done]
        elif a.refetch_stored:
            cur.execute("SELECT nct_id FROM ct_trial ORDER BY nct_id")
            ids = [r["nct_id"] for r in cur.fetchall()]
        got = 0
        for i in range(0, len(ids), BATCH):
            parsed = [row(st) for st in fetch(ids[i:i + BATCH])]
            parsed = [(r, mk) for r, mk in parsed if r[0] and r[11]]          # with results only
            rows = [r for r, _ in parsed]
            cur.executemany("""REPLACE INTO ct_trial (nct_id, title, status, why_stopped, phase, enrollment, start_date,
                    primary_completion_date, primary_completion_type, completion_date, results_first_posted,
                    has_results, allocation, masking, controlled, biomarker_kind, biomarker_text)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", rows)
            if rows:
                cur.execute(f"DELETE FROM ct_trial_marker WHERE nct_id IN ({','.join(['%s'] * len(rows))})",
                            [r[0] for r in rows])
                cur.executemany("INSERT INTO ct_trial_marker (nct_id, marker, kind) VALUES (%s, %s, %s)",
                                [(r[0], m[:60], k) for r, mk in parsed for m, k in mk.items()])
            con.commit()
            got += len(rows)
            if (i // BATCH) % 20 == 0 or i + BATCH >= len(ids):
                print(f"{min(i + BATCH, len(ids))}/{len(ids)} NCT numbers, {got} with results stored",
                      file=sys.stderr, flush=True)
            time.sleep(0.5)
    summarise(con, cur)
    cur.execute("""SELECT COUNT(*) AS indications, SUM(trial_n > 0) AS with_trials,
                          SUM(trial_last_primary_completion IS NOT NULL) AS with_primary_completion,
                          SUM(trial_n_results > 0) AS with_results, SUM(trial_n_dna > 0) AS with_dna_test,
                          SUM(trial_n_biomarker > 0) AS with_any_marker FROM drug_indication""")
    print(cur.fetchone())


if __name__ == "__main__":
    main()
