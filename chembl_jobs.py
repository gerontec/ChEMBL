#!/usr/bin/env python3
"""Batch jobs for large ("monster") queries: queue in table x_job, one worker, result file + mail.

A job runs one API endpoint for a list of names, e.g. /targets/{name}/candidates for 500 targets, pages through
every result without the 10 000 rows per request limit of the interactive API, writes one gzip file
(CSV or JSON Lines, first column 'query') and mails a download link.

    POST /jobs            submit  (chembl_api.py)
    GET  /jobs/{id}       status
    GET  /jobs/{id}/result download (the job id is the secret)

Worker:  ./chembl_jobs.py            (systemd: chembl-jobs.service; one job at a time)

Environment (worker and API):
  CHEMBL_JOB_DIR            result files                      (default ./jobs)
  CHEMBL_JOB_KEEP_DAYS      delete results after N days       (default 7)
  CHEMBL_JOB_MAX_ROWS       stop a job after N rows           (default 5000000)
  CHEMBL_JOB_STATEMENT_TIME SQL statement limit in the worker (default 900 s)
  CHEMBL_PUBLIC_URL         base URL used in mails            (default http://localhost:9099)
  CHEMBL_MAIL_FROM          sender                            (default chembl@localhost)
  CHEMBL_SMTP_HOST          SMTP relay, port 25               (default localhost; empty = no mail)
"""

import csv
import gzip
import json
import os
import smtplib
import sys
import time
import traceback
from email.message import EmailMessage
from email.utils import formatdate

import chembl

JOB_DIR = os.path.abspath(os.environ.get("CHEMBL_JOB_DIR", "jobs"))
KEEP_DAYS = int(os.environ.get("CHEMBL_JOB_KEEP_DAYS", "7"))
MAX_ROWS = int(os.environ.get("CHEMBL_JOB_MAX_ROWS", "5000000"))
STATEMENT_TIME = os.environ.get("CHEMBL_JOB_STATEMENT_TIME", "900")
PUBLIC_URL = os.environ.get("CHEMBL_PUBLIC_URL", "http://localhost:9099").rstrip("/")
MAIL_FROM = os.environ.get("CHEMBL_MAIL_FROM", "chembl@localhost")
SMTP_HOST = os.environ.get("CHEMBL_SMTP_HOST", "localhost")
PAGE = 10000

# endpoint template -> key of the row list in the response (None = response is the list itself)
ENDPOINTS = {
    "/compounds/{name}/targets": "targets",
    "/compounds/{name}/candidates": "candidates",
    "/compounds/{name}/mechanisms": None,
    "/compounds/{name}/activities": None,
    "/compounds/{name}/indications": None,
    "/targets/{name}/compounds": "compounds",
    "/targets/{name}/candidates": "candidates",
    "/targets/{name}/mechanisms": None,
    "/indications/{name}": "compounds",
}

DDL = """
CREATE TABLE IF NOT EXISTS x_job (
    job_id     CHAR(32)     NOT NULL PRIMARY KEY,
    created    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started    DATETIME     NULL,
    finished   DATETIME     NULL,
    status     ENUM('queued','running','done','failed') NOT NULL DEFAULT 'queued',
    email      VARCHAR(254) NOT NULL DEFAULT '' COMMENT 'optional, empty = no mail',
    client_ip  VARCHAR(45)  NOT NULL,
    endpoint   VARCHAR(64)  NOT NULL,
    names      MEDIUMTEXT   NOT NULL COMMENT 'JSON list',
    params     TEXT         NOT NULL COMMENT 'JSON object',
    format     ENUM('csv','jsonl') NOT NULL DEFAULT 'csv',
    n_names    INT          NOT NULL,
    n_done     INT          NOT NULL DEFAULT 0,
    n_rows     BIGINT       NULL,
    n_bytes    BIGINT       NULL,
    n_errors   INT          NULL,
    error      TEXT         NULL,
    mailed     DATETIME     NULL,
    KEY idx_xjob_status (status, created),
    KEY idx_xjob_ip (client_ip, created),
    KEY idx_xjob_email (email, created)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci COMMENT='batch jobs of chembl_api.py'
"""


def ensure_schema(cur):
    cur.execute(DDL)


def result_path(job_id, fmt):
    return os.path.join(JOB_DIR, f"{job_id}.{'csv' if fmt == 'csv' else 'jsonl'}.gz")


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


# ---------------------------------------------------------------- running

def job_endpoint_pages(path):
    """Endpoints with limit/offset paging (the response carries 'total')."""
    return path.startswith("/targets/") and path.endswith(("/compounds", "/candidates"))

def fetch_all(client, path, params, key):
    """GET one endpoint, paging with limit/offset where the response has 'total'."""
    p = dict(params)
    pages = key in ("compounds", "candidates") and job_endpoint_pages(path)
    if pages:
        p.setdefault("limit", PAGE)
        p["limit"] = min(int(p["limit"]), PAGE)
        p["offset"] = 0
    rows = []
    while True:
        r = client.get(path, params=p)
        if r.status_code != 200:
            raise LookupError(f"{r.status_code} {r.text[:300]}")
        data = r.json()
        part = data if key is None else data.get(key, [])
        rows.extend(part)
        if not (pages and isinstance(data, dict) and "total" in data):
            return rows
        p["offset"] += len(part)
        if not part or p["offset"] >= data["total"] or len(rows) >= MAX_ROWS:
            return rows


def run_job(client, job):
    names = json.loads(job["names"])
    params = json.loads(job["params"])
    key = ENDPOINTS[job["endpoint"]]
    os.makedirs(JOB_DIR, exist_ok=True)
    final = result_path(job["job_id"], job["format"])
    tmp = final + ".part"
    n_rows, errors, fields, writer = 0, [], None, None
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as out:
        for i, name in enumerate(names, 1):
            path = job["endpoint"].replace("{name}", str(name).replace("/", "%2F"))
            try:
                rows = fetch_all(client, path, params, key)
            except LookupError as e:
                errors.append(f"{name}: {e}")
                rows = []
            for r in rows:
                rec = {"query": name, **{k: v for k, v in r.items() if not isinstance(v, (list, dict))}}
                if job["format"] == "jsonl":
                    out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                else:
                    if writer is None:
                        fields = list(rec.keys())
                        writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
                        writer.writeheader()
                    writer.writerow({k: ("" if v is None else v) for k, v in rec.items()})
                n_rows += 1
            if n_rows >= MAX_ROWS:
                errors.append(f"stopped after {MAX_ROWS} rows (CHEMBL_JOB_MAX_ROWS)")
                break
            if i % 20 == 0 or i == len(names):
                yield i, n_rows
        if errors:
            # errors go into the file too, so the result is self-describing
            if job["format"] == "jsonl":
                for e in errors:
                    out.write(json.dumps({"query": None, "error": e}) + "\n")
            else:
                out.write("".join(f"# error: {e}\n" for e in errors))
    os.replace(tmp, final)
    job["_result"] = (n_rows, os.path.getsize(final), errors)


# ------------------------------------------------------------------- mail

def send_mail(job, cur):
    if not SMTP_HOST or not job.get("email"):
        return                                   # mail is optional; without it the client polls /jobs/{id}
    jid = job["job_id"]
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Date"] = MAIL_FROM, job["email"], formatdate(localtime=True)
    if job["status"] == "done":
        msg["Subject"] = f"ChEMBL job {jid[:8]} done: {job['n_rows']} rows"
        body = (f"Your ChEMBL batch job is finished.\n\n"
                f"  endpoint : {job['endpoint']}\n"
                f"  names    : {job['n_names']}\n"
                f"  rows     : {job['n_rows']}\n"
                f"  size     : {job['n_bytes'] / 1e6:.1f} MB gzip ({job['format']})\n"
                f"  errors   : {job['n_errors']}\n\n"
                f"Download (kept {KEEP_DAYS} days):\n  {PUBLIC_URL}/jobs/{jid}/result\n\n"
                f"Status:\n  {PUBLIC_URL}/jobs/{jid}\n")
        if job.get("error"):
            body += "\nFirst errors:\n" + job["error"][:2000] + "\n"
    else:
        msg["Subject"] = f"ChEMBL job {jid[:8]} failed"
        body = (f"Your ChEMBL batch job failed.\n\n  endpoint : {job['endpoint']}\n"
                f"  error    : {(job.get('error') or '')[:2000]}\n\nStatus:\n  {PUBLIC_URL}/jobs/{jid}\n")
    body += f"\n-- \n{PUBLIC_URL}/docs  (source: https://github.com/gerontec/ChEMBL)\n"
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, 25, timeout=30) as s:
        s.send_message(msg)
    cur.execute("UPDATE x_job SET mailed = NOW() WHERE job_id = %s", (jid,))


# ---------------------------------------------------------------- cleanup

def cleanup():
    if not os.path.isdir(JOB_DIR):
        return
    limit = time.time() - KEEP_DAYS * 86400
    for f in os.listdir(JOB_DIR):
        p = os.path.join(JOB_DIR, f)
        if os.path.isfile(p) and os.path.getmtime(p) < limit:
            os.remove(p)
            log(f"removed {f}")


# ------------------------------------------------------------------- main

def main():
    os.environ["CHEMBL_MAX_STATEMENT_TIME"] = STATEMENT_TIME
    os.environ["CHEMBL_RATE_PER_MIN"] = "0"
    os.environ["CHEMBL_ALLOW"] = ""
    from fastapi.testclient import TestClient
    import chembl_api
    client = TestClient(chembl_api.app, client=("127.0.0.1", 0))

    db = chembl.connect(read_timeout=3600)
    db.autocommit(True)
    cur = db.cursor()
    ensure_schema(cur)
    cur.execute("UPDATE x_job SET status = 'queued', started = NULL WHERE status = 'running'")  # after a crash
    log(f"worker started, results in {JOB_DIR}")
    last_cleanup = 0
    while True:
        if time.time() - last_cleanup > 3600:
            cleanup()
            last_cleanup = time.time()
        db.ping(reconnect=True)
        cur.execute("SELECT * FROM x_job WHERE status = 'queued' ORDER BY created LIMIT 1")
        job = cur.fetchone()
        if not job:
            time.sleep(5)
            continue
        cur.execute("UPDATE x_job SET status = 'running', started = NOW() WHERE job_id = %s AND status = 'queued'",
                    (job["job_id"],))
        if cur.rowcount != 1:
            continue
        t = time.time()
        log(f"job {job['job_id'][:8]} {job['endpoint']} x {job['n_names']} -> {job['format']}")
        try:
            for done, rows in run_job(client, job):
                cur.execute("UPDATE x_job SET n_done = %s, n_rows = %s WHERE job_id = %s", (done, rows, job["job_id"]))
            n_rows, n_bytes, errors = job["_result"]
            cur.execute("UPDATE x_job SET status = 'done', finished = NOW(), n_rows = %s, n_bytes = %s, "
                        "n_errors = %s, error = %s WHERE job_id = %s",
                        (n_rows, n_bytes, len(errors), "\n".join(errors[:50]) or None, job["job_id"]))
            log(f"job {job['job_id'][:8]} done: {n_rows} rows, {n_bytes / 1e6:.1f} MB, {len(errors)} errors, "
                f"{time.time() - t:.0f} s")
        except Exception as e:
            cur.execute("UPDATE x_job SET status = 'failed', finished = NOW(), error = %s WHERE job_id = %s",
                        (f"{type(e).__name__}: {e}"[:5000], job["job_id"]))
            log(f"job {job['job_id'][:8]} failed: {e}\n{traceback.format_exc()}")
        cur.execute("SELECT * FROM x_job WHERE job_id = %s", (job["job_id"],))
        try:
            send_mail(cur.fetchone(), cur)
        except Exception as e:
            log(f"mail for {job['job_id'][:8]} failed: {e}")


if __name__ == "__main__":
    sys.exit(main())
