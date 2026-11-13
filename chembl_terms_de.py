#!/usr/bin/env python3
"""German names for the disease terms of drug_indication, for the German disease search.

ChEMBL names indications in English (EFO term, MeSH heading). This script adds four columns to
drug_indication and fills them with German Wikidata labels and aliases (data CC0):
    efo_term_de        German name of efo_id, e.g. "Kolorektales Karzinom" for MONDO:0005575
    efo_synonyms_de    its German aliases separated by |, e.g. "Darmkrebs|Kolonkarzinom"
    mesh_heading_de    German name of mesh_id (Wikidata P486 = MeSH descriptor ID)
    mesh_synonyms_de   its German aliases
efo_id holds MONDO, EFO, DOID, Orphanet and HP identifiers; each has its own Wikidata property.
The API also matches a disease pattern against these columns.

Run it after each ChEMBL import (the columns are not part of the ChEMBL dump):
    python3 chembl_terms_de.py            # add/refresh the columns
    python3 chembl_terms_de.py --stats    # coverage only
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import chembl

SPARQL = "https://query.wikidata.org/sparql"
AGENT = "chembl-disease-terms-de/1.0 (https://github.com/gerontec/ChEMBL)"
BATCH = 200

DDL = """ALTER TABLE drug_indication
    ADD COLUMN IF NOT EXISTS efo_term_de VARCHAR(300) COMMENT 'German Wikidata label of efo_id (chembl_terms_de.py)',
    ADD COLUMN IF NOT EXISTS efo_synonyms_de TEXT COMMENT 'German Wikidata aliases of efo_id, | separated',
    ADD COLUMN IF NOT EXISTS mesh_heading_de VARCHAR(300) COMMENT 'German Wikidata label of mesh_id (chembl_terms_de.py)',
    ADD COLUMN IF NOT EXISTS mesh_synonyms_de TEXT COMMENT 'German Wikidata aliases of mesh_id, | separated'"""


# ChEMBL id prefix -> (Wikidata property, ChEMBL id -> Wikidata value)
EFO_PROPS = {
    "MONDO": ("P5270", lambda i: i.replace(":", "_")),       # MONDO_0005575
    "EFO": ("P11956", lambda i: i.split(":", 1)[1]),           # 0003835
    "DOID": ("P699", lambda i: i),                             # DOID:9256
    "Orphanet": ("P1550", lambda i: i.split(":", 1)[1]),       # 413
    "HP": ("P3841", lambda i: i),                              # HP:0000123
}


def wikidata_de(values, prop="P486"):
    """{value: (wikidata_id, label_de, [aliases_de])} for one batch of identifier values of a Wikidata property."""
    values = " ".join(f'"{m}"' for m in values)
    query = f"""SELECT ?m ?i ?l (GROUP_CONCAT(DISTINCT ?a; separator="|") AS ?al) WHERE {{
        VALUES ?m {{ {values} }} ?i wdt:{prop} ?m . ?i rdfs:label ?l FILTER(lang(?l) = "de")
        OPTIONAL {{ ?i skos:altLabel ?a FILTER(lang(?a) = "de") }} }} GROUP BY ?m ?i ?l"""
    req = urllib.request.Request(SPARQL, data=urllib.parse.urlencode({"query": query, "format": "json"}).encode(),
                                 headers={"User-Agent": AGENT, "Accept": "application/sparql-results+json"})
    for attempt in range(4):        # the public endpoint answers 429/502/503 now and then
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                rows = json.load(r)["results"]["bindings"]
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))
    out = {}
    for b in rows:          # several Wikidata items can carry one MeSH ID: merge their names
        m = b["m"]["value"]
        qid = b["i"]["value"].rsplit("/", 1)[-1]
        label = b["l"]["value"]
        aliases = [a for a in b.get("al", {}).get("value", "").split("|") if a]
        if m in out:
            q0, l0, a0 = out[m]
            out[m] = (q0, l0, a0 + [x for x in [label] + aliases if x != l0 and x not in a0])
        else:
            out[m] = (qid, label, aliases)
    return out


def fill(con, cur, col, prop, ids):
    """Write the German names of ids {wikidata value: chembl id} into <col>_..._de of drug_indication."""
    name, syn, key = {"mesh": ("mesh_heading_de", "mesh_synonyms_de", "mesh_id"),
                      "efo": ("efo_term_de", "efo_synonyms_de", "efo_id")}[col]
    vals = sorted(ids)
    n = 0
    for i in range(0, len(vals), BATCH):
        got = wikidata_de(vals[i:i + BATCH], prop)
        cur.executemany(f"UPDATE drug_indication SET {name} = %s, {syn} = %s WHERE {key} = %s",
                        [(l[:300], "|".join(al)[:65000] or None, ids[v]) for v, (q, l, al) in got.items()])
        con.commit()
        n += len(got)
        print(f"{key} {prop}: {min(i + BATCH, len(vals))}/{len(vals)}, {n} with a German name", file=sys.stderr)
        time.sleep(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stats", action="store_true", help="only show the coverage")
    a = ap.parse_args()
    con = chembl.connect()
    cur = con.cursor()
    cur.execute(DDL)
    if not a.stats:
        cur.execute("SELECT DISTINCT mesh_id FROM drug_indication WHERE mesh_id IS NOT NULL ORDER BY mesh_id")
        fill(con, cur, "mesh", "P486", {r["mesh_id"]: r["mesh_id"] for r in cur.fetchall()})
        cur.execute("SELECT DISTINCT efo_id FROM drug_indication WHERE efo_id IS NOT NULL ORDER BY efo_id")
        efo = [r["efo_id"] for r in cur.fetchall()]
        for prefix, (prop, conv) in EFO_PROPS.items():
            fill(con, cur, "efo", prop, {conv(i): i for i in efo if i.startswith(prefix + ":")})
    cur.execute("""SELECT COUNT(DISTINCT mesh_id) AS mesh_ids,
                          COUNT(DISTINCT CASE WHEN mesh_heading_de IS NOT NULL THEN mesh_id END) AS mesh_de,
                          COUNT(DISTINCT efo_id) AS efo_ids,
                          COUNT(DISTINCT CASE WHEN efo_term_de IS NOT NULL THEN efo_id END) AS efo_de,
                          ROUND(AVG(mesh_heading_de IS NOT NULL OR efo_term_de IS NOT NULL), 3) AS row_share
                   FROM drug_indication""")
    print(cur.fetchone())


if __name__ == "__main__":
    main()
