#!/usr/bin/env python3
"""REST API with Swagger UI over a local ChEMBL mirror (MariaDB/MySQL schema 'chembl').

Forward:  compound -> targets       GET /compounds/{name}/targets
Reverse:  target   -> compounds     GET /targets/{name}/compounds
Evidence: x_mech_evidence           GET /compounds/{name}/candidates, /targets/{name}/candidates

All SQL lives in chembl.py (same directory); this module only maps it to HTTP.
Run:  python3 -m uvicorn chembl_api:app --host 0.0.0.0 --port 9099   (see chembl-api.service)
Docs: http://<host>:9099/docs
"""

import ipaddress
import os
import threading
import time
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import chembl

try:
    import redis
except ImportError:          # optional: without redis the rate limit is per worker process
    redis = None


# ---------------------------------------------------------------- settings (environment)
#   CHEMBL_ALLOW               networks allowed to connect at all, comma separated (empty = everyone)
#   CHEMBL_TRUSTED_PROXIES     reverse proxies whose X-Forwarded-For is used as client address
#   CHEMBL_RATE_PER_MIN        requests per minute and client behind a trusted proxy (0 = off)
#   CHEMBL_MAX_STATEMENT_TIME  seconds per SQL statement (MariaDB max_statement_time, 0 = off)
#   CHEMBL_CACHE_MAX_AGE       Cache-Control max-age for successful GET responses (0 = no header)
#   CHEMBL_REDIS_URL           redis for a rate limit shared by all workers (default redis://127.0.0.1:6379/0)

def _nets(var):
    return [ipaddress.ip_network(n.strip(), strict=False) for n in os.environ.get(var, "").split(",") if n.strip()]


ALLOW = _nets("CHEMBL_ALLOW")
TRUSTED_PROXIES = _nets("CHEMBL_TRUSTED_PROXIES")
RATE_PER_MIN = int(os.environ.get("CHEMBL_RATE_PER_MIN", "120"))
MAX_STATEMENT_TIME = float(os.environ.get("CHEMBL_MAX_STATEMENT_TIME", "30"))
CACHE_MAX_AGE = int(os.environ.get("CHEMBL_CACHE_MAX_AGE", "86400"))

_redis = None
if redis is not None and RATE_PER_MIN:
    try:
        _redis = redis.Redis.from_url(os.environ.get("CHEMBL_REDIS_URL", "redis://127.0.0.1:6379/0"),
                                      socket_timeout=0.5, socket_connect_timeout=0.5)
        _redis.ping()
    except Exception:
        _redis = None
_local_hits, _local_lock = {}, threading.Lock()


def _in(addr, nets):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in n for n in nets)


def _over_limit(client):
    """Fixed one-minute window per client. True if the client exceeded RATE_PER_MIN."""
    window = int(time.time() // 60)
    key = f"chembl:rl:{client}:{window}"
    if _redis is not None:
        try:
            pipe = _redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, 120)
            return pipe.execute()[0] > RATE_PER_MIN
        except Exception:
            pass                                    # redis gone: fall back to the local counter
    with _local_lock:
        for k in [k for k in _local_hits if not k.endswith(f":{window}")]:
            del _local_hits[k]
        _local_hits[key] = _local_hits.get(key, 0) + 1
        return _local_hits[key] > RATE_PER_MIN

app = FastAPI(
    title="ChEMBL local API",
    version="1.0",
    description=(
        "Queries against a local ChEMBL mirror (MariaDB schema `chembl`).\n\n"
        "* **Compound → targets**: `/compounds/{name}/targets` — name, synonym or ChEMBL ID "
        "(e.g. `ivermectin`, `CHEMBL6067487`). Salts/forms are included via molecule_hierarchy unless `exact=true`.\n"
        "* **Target → compounds**: `/targets/{name}/compounds` — target ChEMBL ID, UniProt accession, "
        "gene symbol or exact target name (e.g. `CHEMBL240`, `P08183`, `ABCB1`). Gene symbols match all organisms; "
        "narrow with `organism=Homo sapiens`.\n"
        "* `pchembl` = −log10(molar IC50/EC50/Ki/Kd…); 6 = 1 µM, 9 = 1 nM. "
        "`max_phase` 4 = approved drug. `conf` = ChEMBL assay confidence score (9 = direct single protein).\n"
        "* Target `CHEMBL612545` 'Unchecked' collects assays without a target assignment."
    ),
)


@app.middleware("http")
async def guard(request: Request, call_next):
    peer = request.client.host if request.client else ""
    if ALLOW and not _in(peer, ALLOW):
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    if RATE_PER_MIN and _in(peer, TRUSTED_PROXIES):
        fwd = request.headers.get("x-forwarded-for", "")
        client = fwd.split(",")[-1].strip() if fwd else peer    # the proxy appends the real client last
        if _over_limit(client):
            return JSONResponse({"detail": f"rate limit {RATE_PER_MIN} requests per minute"},
                                status_code=429, headers={"Retry-After": "60"})
    response = await call_next(request)
    if CACHE_MAX_AGE and request.method == "GET" and response.status_code == 200:
        response.headers.setdefault("Cache-Control", f"public, max-age={CACHE_MAX_AGE}")
    return response


def db_cursor():
    conn = chembl.connect()
    try:
        cur = conn.cursor()
        if MAX_STATEMENT_TIME:
            cur.execute("SET SESSION max_statement_time = %s", (MAX_STATEMENT_TIME,))
        yield cur
    finally:
        conn.close()


def strip_tid(rows):
    for r in rows:
        r.pop("tid", None)
    return rows


def compound_molregnos(cur, name, exact):
    hits = chembl.resolve(cur, name)
    if not hits:
        cand = chembl.search(cur, name, 15)
        raise HTTPException(404, {"error": f"no compound '{name}'",
                                  "candidates": [{"chembl_id": c["chembl_id"], "pref_name": c["pref_name"]}
                                                 for c in cand]})
    molregnos = [h["molregno"] for h in hits]
    return molregnos if exact else chembl.family(cur, molregnos)


def compound_forms(cur, molregnos):
    ph = ",".join(["%s"] * len(molregnos))
    return chembl.q(cur, f"SELECT chembl_id, pref_name, max_phase, molecule_type FROM molecule_dictionary "
                         f"WHERE molregno IN ({ph}) ORDER BY max_phase DESC, chembl_id", tuple(molregnos))


def target_rows(cur, name, organism, all_types):
    rows = chembl.resolve_target(cur, name, organism, not all_types)
    if not rows:
        cand = chembl.search_targets(cur, name, organism, 15)
        raise HTTPException(404, {"error": f"no target '{name}'" + (f" in {organism}" if organism else ""),
                                  "hint": "try /targets/search, all_types=true or another organism",
                                  "candidates": strip_tid(cand)})
    return rows


# ------------------------------------------------------------------ meta

@app.get("/version", tags=["meta"], summary="ChEMBL release and component versions")
def version(cur=Depends(db_cursor)):
    return chembl.q(cur, "SELECT name, creation_date, comments FROM version ORDER BY name")


# ------------------------------------------------------------- compounds

@app.get("/compounds/search", tags=["compounds"], summary="Prefix search over compound names and synonyms")
def compounds_search(q: str = Query(..., min_length=2, examples=["iverm"]),
                     limit: int = Query(40, ge=1, le=500), cur=Depends(db_cursor)):
    rows = chembl.search(cur, q, limit)
    for r in rows:
        r.pop("molregno", None)
    return rows


@app.get("/compounds/{name}", tags=["compounds"], summary="Resolve a compound and list its forms")
def compound(name: str, exact: bool = False, cur=Depends(db_cursor)):
    return {"query": name, "forms": compound_forms(cur, compound_molregnos(cur, name, exact))}


@app.get("/compounds/{name}/mechanisms", tags=["compounds"],
         summary="Curated mechanism of action (drug_mechanism)")
def compound_mechanisms(name: str, exact: bool = False, cur=Depends(db_cursor)):
    return chembl.mechanisms(cur, compound_molregnos(cur, name, exact))


@app.get("/compounds/{name}/targets", tags=["compounds"],
         summary="Compound → all targets with activity data (one row per target)")
def compound_targets(name: str,
                     human: bool = Query(False, description="only Homo sapiens targets"),
                     min_n: int = Query(1, ge=1, description="min. activity records per target"),
                     min_pchembl: Optional[float] = Query(None, description="min. best pChEMBL"),
                     limit: Optional[int] = Query(None, ge=1, le=10000),
                     exact: bool = False,
                     mechanisms: bool = Query(True, description="include curated mechanisms"),
                     cur=Depends(db_cursor)):
    molregnos = compound_molregnos(cur, name, exact)
    out = {"query": name, "forms": compound_forms(cur, molregnos)}
    if mechanisms:
        out["mechanisms"] = chembl.mechanisms(cur, molregnos)
    out["targets"] = chembl.activity_targets(cur, molregnos, human, min_n, min_pchembl, limit)
    return out


@app.get("/compounds/{name}/activities", tags=["compounds"],
         summary="Single activity records of a compound on one target")
def compound_activities(name: str,
                        target: str = Query(..., description="target ChEMBL ID", examples=["CHEMBL4302"]),
                        exact: bool = False, cur=Depends(db_cursor)):
    return chembl.activity_detail(cur, compound_molregnos(cur, name, exact), target)


LEVELS_DOC = ("evidence letters: A curated (drug_mechanism), B molecular conf≥8 pChEMBL≥7 in ≥2 documents, "
              "C molecular conf≥8 pChEMBL≥6, D molecular pChEMBL≥5, P phenotypic pChEMBL≥5, '-' measured below 5")


def check_levels(levels):
    try:
        chembl._levels(levels)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return levels


@app.get("/compounds/{name}/candidates", tags=["evidence"],
         summary="Compound → every target with an evidence level (curated + measured + phenotypic)")
def compound_candidates(name: str,
                        levels: str = Query("ABCDP", description=LEVELS_DOC),
                        human: bool = False, exact: bool = False,
                        limit: Optional[int] = Query(None, ge=1, le=100000),
                        cur=Depends(db_cursor)):
    check_levels(levels)
    molregnos = compound_molregnos(cur, name, exact)
    rows = chembl.candidates_for_compound(cur, molregnos, levels, human, limit)
    return {"query": name, "levels": levels, "count": len(rows), "candidates": rows}


@app.get("/targets/{name}/candidates", tags=["evidence"],
         summary="Target → every compound with an evidence level (curated + measured)")
def target_candidates(name: str,
                      levels: str = Query("ABCD", description=LEVELS_DOC),
                      organism: Optional[str] = None, all_types: bool = False,
                      min_phase: Optional[float] = Query(None, description="min. clinical phase (4 = approved)"),
                      named: bool = Query(False, description="only compounds with a preferred name"),
                      limit: int = Query(200, ge=1, le=10000), offset: int = Query(0, ge=0),
                      cur=Depends(db_cursor)):
    check_levels(levels)
    targets = target_rows(cur, name, organism, all_types)
    rows, total = chembl.candidates_for_target(cur, [t["tid"] for t in targets], levels, min_phase, named,
                                               limit, offset)
    return {"query": name, "levels": levels, "targets": strip_tid(targets),
            "total": total, "offset": offset, "count": len(rows), "candidates": rows}


@app.get("/compounds/{name}/indications", tags=["compounds"],
         summary="Indications of a compound with trial phase and reference types")
def compound_indications(name: str, exact: bool = False, cur=Depends(db_cursor)):
    return chembl.indications_for_compound(cur, compound_molregnos(cur, name, exact))


# ----------------------------------------------------------- indications

@app.get("/indications/{disease}", tags=["indications"],
         summary="Disease → all compounds that reached a clinical trial for it (any outcome)")
def disease_indications(disease: str,
                        min_phase: Optional[float] = Query(None, description="min. phase for this indication"),
                        cur=Depends(db_cursor)):
    """Prefix match on EFO term or MeSH heading, e.g. `COVID-19`. `phase_for_indication` is the highest
    trial phase reached for this indication; it does not say whether the trial was positive."""
    rows = chembl.indications_for_disease(cur, disease, min_phase)
    if not rows:
        raise HTTPException(404, {"error": f"no indication matching '{disease}%'"})
    return {"query": disease, "count": len(rows), "compounds": rows}


# --------------------------------------------------------------- targets

@app.get("/targets/search", tags=["targets"],
         summary="Prefix search over target names and gene symbols (q='%' lists all targets of an organism)")
def targets_search(q: str = Query(..., min_length=1, examples=["KCNH"]),
                   organism: Optional[str] = Query(None, examples=["Homo sapiens"]),
                   limit: int = Query(50, ge=1, le=500), cur=Depends(db_cursor)):
    return strip_tid(chembl.search_targets(cur, q, organism, limit))


@app.get("/targets/{name}", tags=["targets"],
         summary="Resolve a target (ChEMBL ID, UniProt accession, gene symbol, exact name)")
def target(name: str, organism: Optional[str] = None,
           all_types: bool = Query(False, description="also complexes/families containing the gene"),
           cur=Depends(db_cursor)):
    return strip_tid(target_rows(cur, name, organism, all_types))


@app.get("/targets/{name}/mechanisms", tags=["targets"],
         summary="Drugs with a curated mechanism of action on the target")
def target_mechanisms(name: str, organism: Optional[str] = None, all_types: bool = False,
                      cur=Depends(db_cursor)):
    tids = [t["tid"] for t in target_rows(cur, name, organism, all_types)]
    return chembl.target_mechanisms(cur, tids)


@app.get("/targets/{name}/compounds", tags=["targets"],
         summary="Target → compounds with activity data (one row per compound, best pChEMBL first)")
def target_compounds(name: str,
                     organism: Optional[str] = Query(None, examples=["Homo sapiens"]),
                     all_types: bool = Query(False, description="also complexes/families containing the gene"),
                     min_n: int = Query(1, ge=1, description="min. activity records per compound"),
                     min_pchembl: Optional[float] = Query(None, description="only records with pChEMBL ≥ value"),
                     min_phase: Optional[float] = Query(None, description="min. clinical phase (4 = approved)"),
                     named: bool = Query(False, description="only compounds with a preferred name"),
                     smiles: bool = Query(False, description="add canonical SMILES"),
                     limit: int = Query(200, ge=1, le=10000),
                     offset: int = Query(0, ge=0),
                     mechanisms: bool = Query(True, description="include curated mechanisms"),
                     cur=Depends(db_cursor)):
    targets = target_rows(cur, name, organism, all_types)
    tids = [t["tid"] for t in targets]
    rows, total = chembl.target_compounds(cur, tids, min_n, min_pchembl, min_phase, named, limit, offset, smiles)
    out = {"query": name, "targets": strip_tid(targets)}
    if mechanisms:
        out["mechanisms"] = chembl.target_mechanisms(cur, tids)
    out.update({"total": total, "offset": offset, "count": len(rows), "compounds": rows})
    return out
