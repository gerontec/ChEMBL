#!/usr/bin/env python3
"""Batch jobs for large ("monster") queries: queue in table x_job, one worker, result file + mail.

A job runs one API endpoint for a list of names, e.g. /targets/{name}/candidates for 500 targets, pages through
every result without the 10 000 rows per request limit of the interactive API and writes one file that opens
on a plain Windows PC: xlsx (default; sheets 'info', 'results', 'errors'), or csv / jsonl packed as zip.
The first column 'query' holds the name a row belongs to. A download link is mailed if an address was given.

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
import json
import os
import smtplib
import sys
import time
import traceback
import zipfile
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
    format     ENUM('xlsx','csv','jsonl') NOT NULL DEFAULT 'xlsx',
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


FORMATS = {   # format -> (file extension, media type)
    "xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "csv": ("csv.zip", "application/zip"),
    "jsonl": ("jsonl.zip", "application/zip"),
}
XLSX_MAX_ROWS = 1_048_575        # Excel rows per sheet minus the header

LEGEND = [
    ("A", "curated mechanism of action (ChEMBL drug_mechanism)"),
    ("B", "molecular target, assay confidence >= 8, best pChEMBL >= 7, active in >= 2 documents"),
    ("C", "molecular target, assay confidence >= 8, best pChEMBL >= 6"),
    ("D", "molecular target, best pChEMBL >= 5 (about 10 uM)"),
    ("P", "phenotypic target (organism, cell line, tissue), best pChEMBL >= 5"),
    ("-", "measured, best pChEMBL < 5"),
    ("pChEMBL", "-log10 of the molar IC50/EC50/Ki/Kd: 6 = 1 uM, 9 = 1 nM"),
]


def ensure_schema(cur):
    cur.execute(DDL)
    cur.execute("SELECT column_type FROM information_schema.columns WHERE table_schema = DATABASE() "
                "AND table_name = 'x_job' AND column_name = 'format'")
    row = cur.fetchone()
    if row and "xlsx" not in (row["column_type"] if isinstance(row, dict) else row[0]):
        cur.execute("ALTER TABLE x_job MODIFY format ENUM('xlsx','csv','jsonl') NOT NULL DEFAULT 'xlsx'")


def result_path(job_id, fmt):
    return os.path.join(JOB_DIR, f"{job_id}.{FORMATS[fmt][0]}")


def download_name(job_id, fmt):
    return f"chembl_job_{job_id[:8]}.{FORMATS[fmt][0]}"


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


class XlsxOut:
    """Streaming xlsx writer: sheet 'info', then 'results' (continued in 'results 2', … above 1 048 575 rows)."""

    def __init__(self, path, job, info):
        from openpyxl import Workbook
        self.path, self.wb = path, Workbook(write_only=True)
        ws = self.wb.create_sheet("info")
        for k, v in info:
            ws.append([k, v])
        self.ws, self.fields, self.n_sheet, self.part = None, None, 0, 0

    def row(self, rec):
        if self.ws is None or self.n_sheet >= XLSX_MAX_ROWS:
            self.part += 1
            self.ws = self.wb.create_sheet("results" if self.part == 1 else f"results {self.part}")
            self.fields = self.fields or list(rec.keys())
            self.ws.append(self.fields)
            self.n_sheet = 0
        self.ws.append([self._cell(rec.get(f)) for f in self.fields])
        self.n_sheet += 1

    @staticmethod
    def _cell(v):
        """Excel limits: 32 767 characters per cell, no control characters."""
        if isinstance(v, str):
            from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
            v = ILLEGAL_CHARACTERS_RE.sub("", v)
            if len(v) > 32000:
                v = v[:32000] + " …"
        return v

    def close(self, errors):
        if self.ws is None:
            self.wb.create_sheet("results").append(["no rows"])
        if errors:
            ws = self.wb.create_sheet("errors")
            ws.append(["error"])
            for e in errors:
                ws.append([e])
        self.wb.save(self.path)


class TextOut:
    """csv (UTF-8 with BOM, so Excel shows umlauts) or JSON Lines, packed as zip with info.txt and errors.txt."""

    def __init__(self, path, job, info):
        self.path, self.fmt, self.info = path, job["format"], info
        self.inner = download_name(job["job_id"], job["format"])[:-4]          # without .zip
        self.tmp = path + ".data"
        self.f = open(self.tmp, "w", encoding="utf-8-sig" if self.fmt == "csv" else "utf-8", newline="")
        self.writer = None

    def row(self, rec):
        if self.fmt == "jsonl":
            self.f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            return
        if self.writer is None:
            self.writer = csv.DictWriter(self.f, fieldnames=list(rec.keys()), extrasaction="ignore")
            self.writer.writeheader()
        self.writer.writerow({k: ("" if v is None else v) for k, v in rec.items()})

    def close(self, errors):
        self.f.close()
        with zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(self.tmp, self.inner)
            z.writestr("info.txt", "".join(f"{k}: {v}\r\n" for k, v in self.info))
            if errors:
                z.writestr("errors.txt", "".join(f"{e}\r\n" for e in errors))
        os.remove(self.tmp)


def job_info(client, job, params):
    try:
        release = next((v["name"] for v in client.get("/version").json() if v["name"].startswith("ChEMBL_")), "")
    except Exception:
        release = ""
    return ([("job", job["job_id"]), ("created", str(job["created"])), ("endpoint", job["endpoint"]),
             ("parameters", json.dumps(params) if params else "none"), ("names", job["n_names"]),
             ("database", release), ("source", f"{PUBLIC_URL}/docs  ·  https://github.com/gerontec/ChEMBL"),
             ("", "")] + [(f"legend {k}", v) for k, v in LEGEND])


def run_job(client, job):
    names = json.loads(job["names"])
    params = json.loads(job["params"])
    key = ENDPOINTS[job["endpoint"]]
    os.makedirs(JOB_DIR, exist_ok=True)
    final = result_path(job["job_id"], job["format"])
    tmp = final + ".part"
    info = job_info(client, job, params)
    out = XlsxOut(tmp, job, info) if job["format"] == "xlsx" else TextOut(tmp, job, info)
    n_rows, errors = 0, []
    for i, name in enumerate(names, 1):
        path = job["endpoint"].replace("{name}", str(name).replace("/", "%2F"))
        try:
            rows = fetch_all(client, path, params, key)
        except LookupError as e:
            errors.append(f"{name}: {e}")
            rows = []
        for r in rows:
            out.row({"query": name, **{k: v for k, v in r.items() if not isinstance(v, (list, dict))}})
            n_rows += 1
        if n_rows >= MAX_ROWS:
            errors.append(f"stopped after {MAX_ROWS} rows (CHEMBL_JOB_MAX_ROWS)")
            break
        if i % 20 == 0 or i == len(names):
            yield i, n_rows
    out.close(errors)
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
                f"  file     : {download_name(jid, job['format'])}, {job['n_bytes'] / 1e6:.1f} MB\n"
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
