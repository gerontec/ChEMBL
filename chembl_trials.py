#!/usr/bin/env python3
"""Dates and status of the clinical trials behind drug_indication, from ClinicalTrials.gov.

ChEMBL links a drug indication to its trials (indication_refs, ref_type 'ClinicalTrials', a comma list of
NCT numbers) but stores no dates or outcomes. This script loads them from the ClinicalTrials.gov API v2:

  ct_trial          one row per NCT number: title, status, phase, enrollment, start date,
                    primary completion date (the last participant was examined for the primary outcome,
                    i.e. when the main result was measured; ACTUAL or ESTIMATED), completion date,
                    first posting of results, has_results, why_stopped (reason given for a stop)
  ct_indication     drugind_id <-> nct_id
  drug_indication   summary columns per indication: trial_n, trial_n_results, trial_first_start,
                    trial_last_primary_completion (ACTUAL dates only), trial_last_results_posted

Run it after each ChEMBL import and now and then to refresh the trial status:
    python3 chembl_trials.py
    python3 chembl_trials.py --summary-only     # recompute the drug_indication columns
"""
import argparse
import json
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
          "PrimaryCompletionDateType,CompletionDate,ResultsFirstPostDate,HasResults,WhyStopped")

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
        ADD COLUMN IF NOT EXISTS trial_last_results_posted DATE COMMENT 'latest first posting of results'""",
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


def row(st):
    p = st.get("protocolSection", {})
    s = p.get("statusModule", {})
    d = p.get("designModule", {})
    pc = s.get("primaryCompletionDateStruct", {})
    return (p.get("identificationModule", {}).get("nctId"),
            (p.get("identificationModule", {}).get("briefTitle") or "")[:600] or None,
            s.get("overallStatus"), (s.get("whyStopped") or "")[:500] or None, "/".join(d.get("phases", []))[:40] or None,
            d.get("enrollmentInfo", {}).get("count"),
            day(s.get("startDateStruct", {}).get("date")), day(pc.get("date")), pc.get("type"),
            day(s.get("completionDateStruct", {}).get("date")),
            day(s.get("resultsFirstPostDateStruct", {}).get("date")), int(bool(st.get("hasResults"))))


def summarise(con, cur):
    cur.execute("""UPDATE drug_indication di LEFT JOIN (
            SELECT ci.drugind_id, COUNT(*) AS n, SUM(t.has_results) AS n_res, MIN(t.start_date) AS first_start,
                   MAX(CASE WHEN t.primary_completion_type = 'ACTUAL' THEN t.primary_completion_date END) AS last_pc,
                   MAX(t.results_first_posted) AS last_res
            FROM ct_indication ci JOIN ct_trial t ON t.nct_id = ci.nct_id GROUP BY ci.drugind_id) a
            ON a.drugind_id = di.drugind_id
        SET di.trial_n = a.n, di.trial_n_results = a.n_res, di.trial_first_start = a.first_start,
            di.trial_last_primary_completion = a.last_pc, di.trial_last_results_posted = a.last_res""")
    con.commit()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--summary-only", action="store_true", help="only recompute the drug_indication columns")
    a = ap.parse_args()
    con = chembl.connect()
    cur = con.cursor()
    for s in DDL:
        cur.execute(s)
    cur.execute("ALTER TABLE ct_trial ADD COLUMN IF NOT EXISTS why_stopped VARCHAR(500) AFTER status")
    if not a.summary_only:
        cur.execute("SELECT drugind_id, ref_id FROM indication_refs WHERE ref_type = 'ClinicalTrials'")
        links = {(r["drugind_id"], n.strip()) for r in cur.fetchall() for n in r["ref_id"].split(",")
                 if n.strip().startswith("NCT")}
        cur.execute("DELETE FROM ct_indication")
        cur.executemany("INSERT INTO ct_indication (drugind_id, nct_id) VALUES (%s, %s)", sorted(links))
        con.commit()
        ids = sorted({n for _, n in links})
        got = 0
        for i in range(0, len(ids), BATCH):
            rows = [row(st) for st in fetch(ids[i:i + BATCH])]
            cur.executemany("""REPLACE INTO ct_trial (nct_id, title, status, why_stopped, phase, enrollment, start_date,
                    primary_completion_date, primary_completion_type, completion_date, results_first_posted,
                    has_results) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", [r for r in rows if r[0]])
            con.commit()
            got += len(rows)
            if (i // BATCH) % 20 == 0 or i + BATCH >= len(ids):
                print(f"{min(i + BATCH, len(ids))}/{len(ids)} NCT numbers, {got} found", file=sys.stderr, flush=True)
            time.sleep(0.5)
    summarise(con, cur)
    cur.execute("""SELECT COUNT(*) AS indications, SUM(trial_n > 0) AS with_trials,
                          SUM(trial_last_primary_completion IS NOT NULL) AS with_primary_completion,
                          SUM(trial_n_results > 0) AS with_results FROM drug_indication""")
    print(cur.fetchone())


if __name__ == "__main__":
    main()
