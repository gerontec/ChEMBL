#!/usr/bin/env python3
"""Joins between ChEMBL and the NCBI nr protein database of github.com/gerontec/blastDiamond (tables blast_acc,
blast_seq, blast_taxon in schema CHEMBL_NR_DB on the same MariaDB server).

The link is the UniProt accession: ChEMBL target components carry it (component_sequences.accession, no version),
nr holds Swiss-Prot entries under the same accession with a version (P02766 -> P02766.1). A TrEMBL accession is
not in nr at all; for those the RefSeq accession of the same protein is used instead (table x_uniprot_refseq,
filled by nr_uniprot_map.py from NCBI gene_refseq_uniprotkb_collab), and the entry says so in 'via'. One nr entry (OID) is one
distinct sequence; all its accessions (blast_acc rows of the OID) are identical sequences, often from many
organisms. Homologs (similar, not identical) need a DIAMOND search: CHEMBL_BLAST_URL links to that form.

  compound -> its targets -> their nr entries        compound_nr()
  target   -> its components -> their nr entries     target_nr()
  nr accession -> ChEMBL targets (identical sequence) -> drugs and active compounds   protein_chembl()
  nr entry -> organisms with an identical sequence   organisms()
"""
import os
import re

import chembl

NR_DB = os.environ.get("CHEMBL_NR_DB", "wagodb")
if not re.fullmatch(r"[A-Za-z0-9_]+", NR_DB):
    raise SystemExit(f"CHEMBL_NR_DB: invalid schema name {NR_DB!r}")
BLAST_URL = os.environ.get("CHEMBL_BLAST_URL", "").rstrip("/")

# UniProt accession format (uniprot.org/help/accession_numbers), with an optional version
UNIPROT = re.compile(r"^([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})(?:\.\d+)?$")


def like_prefix(s):
    """LIKE pattern for 'starts with s.': '_' and '%' in accessions (XP_..., WP_...) are wildcards otherwise, and
    'XP_123.%' would range-scan every accession starting with 'XP'."""
    return s.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%") + ".%"


def available(cur):
    """True when the nr tables are readable (the join endpoints answer 503 otherwise)."""
    try:
        chembl.q(cur, f"SELECT 1 FROM {NR_DB}.blast_acc LIMIT 1")
        return True
    except Exception:
        return False


def blast_link(acc=None, seq=None):
    """Link to the DIAMOND form, pre-filled with an nr accession or a sequence."""
    from urllib.parse import quote
    if not BLAST_URL or not (acc or seq):
        return None
    return f"{BLAST_URL}/?acc={quote(acc)}" if acc else f"{BLAST_URL}/?seq={quote(seq)}"


def compound_sequences(cur, molregnos):
    """Own sequences of a peptide/protein compound (e.g. a venom peptide such as ziconotide), each with a link to a
    DIAMOND search of exactly that sequence."""
    if not molregnos:
        return []
    ph = ",".join(["%s"] * len(molregnos))
    rows = chembl.q(cur, f"""
        SELECT DISTINCT d.chembl_id, d.pref_name, b.description, b.component_type, b.organism,
               LENGTH(b.sequence) AS length, b.sequence
        FROM biotherapeutic_components bc
        JOIN bio_component_sequences b ON b.component_id = bc.component_id
        JOIN molecule_dictionary d ON d.molregno = bc.molregno
        WHERE bc.molregno IN ({ph}) AND b.sequence IS NOT NULL AND b.component_type = 'Protein'
        ORDER BY d.chembl_id, b.description""", tuple(molregnos))
    for r in rows:
        seq = re.sub(r"[^A-Za-z]", "", r["sequence"] or "")
        r["homolog_search"] = blast_link(seq=f">{r['chembl_id']}\n{seq}") if seq else None
    return rows


def refseq_of(cur, uniprot_accs):
    """{uniprot accession: [refseq accessions]} from x_uniprot_refseq; {} when the table is missing."""
    accs = sorted(set(a for a in uniprot_accs if a))
    if not accs:
        return {}
    try:
        rows = chembl.q(cur, "SELECT uniprot, refseq FROM x_uniprot_refseq WHERE uniprot IN ("
                        + ",".join(["%s"] * len(accs)) + ")", tuple(accs))
    except Exception:                      # table not filled yet: the direct lookup still works
        return {}
    out = {}
    for r in rows:
        out.setdefault(r["uniprot"], []).append(r["refseq"])
    return out


def nr_entries(cur, uniprot_accs):
    """{uniprot accession: nr entry} for the accessions found in nr. Entry: nr_acc, oid, length, nr_title,
    identical_entries (accessions of the same sequence), organisms (distinct taxa among them), via ('uniprot'
    when the UniProt accession itself is in nr, else the RefSeq accession it was found through)."""
    out = {}
    missing = []
    for u in sorted(set(a for a in uniprot_accs if a)):
        rows = chembl.q(cur, f"SELECT a.oid, a.acc FROM {NR_DB}.blast_acc a WHERE a.acc LIKE %s "
                             "ORDER BY a.acc DESC LIMIT 1", (like_prefix(u),))      # newest version first
        if rows:
            out[u] = {"nr_acc": rows[0]["acc"], "oid": rows[0]["oid"], "via": "uniprot"}
        else:
            missing.append(u)
    for u, refs in refseq_of(cur, missing).items():      # TrEMBL and the like: the same protein as a RefSeq entry
        for r in refs:
            rows = chembl.q(cur, f"SELECT a.oid, a.acc FROM {NR_DB}.blast_acc a WHERE a.acc LIKE %s "
                                 "ORDER BY a.acc DESC LIMIT 1", (like_prefix(r),))
            if rows:
                out[u] = {"nr_acc": rows[0]["acc"], "oid": rows[0]["oid"], "via": r}
                break
    oids = sorted({e["oid"] for e in out.values()})
    if not oids:
        return out
    ph = ",".join(["%s"] * len(oids))
    seq = {r["oid"]: r for r in chembl.q(cur, f"SELECT oid, len, title FROM {NR_DB}.blast_seq WHERE oid IN ({ph})",
                                          tuple(oids))}
    cnt = {r["oid"]: r for r in chembl.q(cur, f"SELECT oid, COUNT(*) AS n, COUNT(DISTINCT taxid) AS taxa "
                                              f"FROM {NR_DB}.blast_acc WHERE oid IN ({ph}) GROUP BY oid", tuple(oids))}
    for e in out.values():
        s, k = seq.get(e["oid"], {}), cnt.get(e["oid"], {})
        e.update(length=s.get("len"), nr_title=(s.get("title") or "")[:300],
                 identical_entries=k.get("n"), organisms=k.get("taxa"), homolog_search=blast_link(e["nr_acc"]))
    return out


def _components(cur, target_chembl_ids):
    """One row per (target, component) of the given targets."""
    if not target_chembl_ids:
        return []
    ph = ",".join(["%s"] * len(target_chembl_ids))
    return chembl.q(cur, f"""
        SELECT td.chembl_id AS target_chembl_id, td.pref_name AS target, td.organism, td.target_type,
               cs.accession AS uniprot, cs.db_source AS uniprot_source
        FROM target_dictionary td
        JOIN target_components tc ON tc.tid = td.tid
        JOIN component_sequences cs ON cs.component_id = tc.component_id
        WHERE td.chembl_id IN ({ph}) AND cs.component_type = 'PROTEIN'
        ORDER BY td.organism = 'Homo sapiens' DESC, td.organism, td.pref_name, cs.accession""",
                    tuple(target_chembl_ids))


def _with_nr(cur, rows):
    """Add the nr entry of every row's UniProt accession (flat columns, None when not in nr)."""
    nr = nr_entries(cur, [r["uniprot"] for r in rows])
    empty = dict.fromkeys(("nr_acc", "oid", "length", "nr_title", "identical_entries", "organisms",
                           "homolog_search", "via"))
    for r in rows:
        r.update(nr.get(r["uniprot"], empty))
        r["in_nr"] = r["uniprot"] in nr
    return rows


def compound_nr(cur, molregnos, human=False, limit=50):
    """Targets of a compound (curated mechanisms first, then activity targets by best pChEMBL, at most limit)
    with the nr entry of each of their protein components."""
    mech = chembl.mechanisms(cur, molregnos)
    act = chembl.activity_targets(cur, molregnos, human=human, limit=limit)
    info = {}
    for m in mech:
        if m.get("target_chembl_id") and (not human or m.get("organism") == "Homo sapiens"):
            info.setdefault(m["target_chembl_id"], {"evidence": "mechanism", "mechanism": m["mechanism_of_action"],
                                                    "best_pchembl": None, "n_act": None})
    for a in act:
        i = info.setdefault(a["target_chembl_id"], {"evidence": "activity", "mechanism": None})
        i.update(best_pchembl=a["best_pchembl"], n_act=a["n_act"])
    rows = _components(cur, list(info))
    for r in rows:
        r.update(info[r["target_chembl_id"]])
    order = {t: k for k, t in enumerate(info)}
    rows.sort(key=lambda r: order[r["target_chembl_id"]])
    return _with_nr(cur, rows)


def target_nr(cur, target_rows):
    """The components of resolved targets (chembl.resolve_target rows) with their nr entries."""
    return _with_nr(cur, _components(cur, [t["target_chembl_id"] for t in target_rows]))


def protein_chembl(cur, acc, compounds_limit=200):
    """An nr accession (any: XP_..., WP_..., P02766.1, ...) -> its nr entry, the UniProt accessions of the same
    sequence, the ChEMBL targets with one of them as component, curated drugs and active compounds."""
    rows = chembl.q(cur, f"SELECT oid, acc FROM {NR_DB}.blast_acc WHERE acc = %s LIMIT 1", (acc,))
    if not rows and "." not in acc:
        rows = chembl.q(cur, f"SELECT oid, acc FROM {NR_DB}.blast_acc WHERE acc LIKE %s ORDER BY acc LIMIT 1",
                        (like_prefix(acc),))
    if not rows:
        return None
    oid = rows[0]["oid"]
    s = chembl.q(cur, f"SELECT len, title FROM {NR_DB}.blast_seq WHERE oid = %s", (oid,))
    accs = [r["acc"] for r in chembl.q(cur, f"SELECT acc FROM {NR_DB}.blast_acc WHERE oid = %s", (oid,))]
    uni = sorted({m.group(1) for m in (UNIPROT.match(a) for a in accs) if m})
    entry = {"nr_acc": rows[0]["acc"], "oid": oid, "length": s[0]["len"] if s else None,
             "nr_title": (s[0]["title"] if s else "")[:300], "identical_entries": len(accs),
             "uniprot": ",".join(uni), "homolog_search": blast_link(rows[0]["acc"])}
    targets, mech, comp = [], [], []
    if uni:
        ph = ",".join(["%s"] * len(uni))
        targets = chembl.q(cur, f"""
            SELECT DISTINCT td.tid, td.chembl_id AS target_chembl_id, td.pref_name AS target, td.organism,
                   td.target_type, cs.accession AS uniprot
            FROM component_sequences cs
            JOIN target_components tc ON tc.component_id = cs.component_id
            JOIN target_dictionary td ON td.tid = tc.tid
            WHERE cs.accession IN ({ph})
            ORDER BY td.target_type = 'SINGLE PROTEIN' DESC, td.pref_name""", tuple(uni))
        tids = sorted({t["tid"] for t in targets})
        if tids:
            mech = chembl.target_mechanisms(cur, tids)
            comp, _ = chembl.target_compounds(cur, tids, limit=compounds_limit)
        for t in targets:
            t.pop("tid", None)
    return {"nr": entry, "targets": targets, "mechanisms": mech, "compounds": comp}


def organisms(cur, oid, limit=500):
    """Organisms with an identical sequence: one row per taxon of the OID's accessions."""
    return chembl.q(cur, f"""
        SELECT a.taxid, t.name AS organism, t.`rank`, COUNT(*) AS entries,
               GROUP_CONCAT(a.acc ORDER BY a.acc SEPARATOR ',') AS accessions
        FROM {NR_DB}.blast_acc a LEFT JOIN {NR_DB}.blast_taxon t ON t.taxid = a.taxid
        WHERE a.oid = %s
        GROUP BY a.taxid, t.name, t.`rank`
        ORDER BY entries DESC, t.name
        LIMIT %s""", (oid, limit))
