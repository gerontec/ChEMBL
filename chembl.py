#!/usr/bin/env python3
"""Query a local ChEMBL mirror (MariaDB/MySQL schema 'chembl') in both directions.

Compound -> targets
    chembl.py ivermectin                 # mechanism-of-action targets + all measured targets
    chembl.py ivermectin --human         # only Homo sapiens targets
    chembl.py ivermectin --min-n 3       # targets with at least 3 activity records
    chembl.py ivermectin --detail CHEMBL4302   # single activity records for one target
    chembl.py iverm --search             # list compounds whose name/synonym starts with 'iverm'
    chembl.py CHEMBL6067487              # ChEMBL IDs work as well
    chembl.py ivermectin --csv > ivm.csv

Target -> compounds
    chembl.py ABCB1 --target --organism "Homo sapiens"   # gene symbol, UniProt, ChEMBL ID or target name
    chembl.py CHEMBL240 --target --min-phase 4           # approved drugs measured on hERG
    chembl.py KCNH --target --search                     # target prefix search

Credentials come from a my.cnf file (host/port/user/password); the schema is always 'chembl'.
  CHEMBL_CNF          path of the file          (default ~/.my.cnf)
  CHEMBL_CNF_SECTION  section to use            (default: first of [chembl], [wagodb], [client] present)
  CHEMBL_DB           schema name               (default chembl)
By default salts and other forms of the compound are included via molecule_hierarchy (--exact disables).
"""

import argparse
import configparser
import csv
import json
import os
import sys
import re
from decimal import Decimal

import pymysql

MYCNF = os.path.expanduser(os.environ.get("CHEMBL_CNF", "~/.my.cnf"))
MYCNF_SECTIONS = ("chembl", "wagodb", "client")
DATABASE = os.environ.get("CHEMBL_DB", "chembl")


def connect(read_timeout=120):
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(MYCNF)
    wanted = os.environ.get("CHEMBL_CNF_SECTION")
    section = wanted or next((s for s in MYCNF_SECTIONS if s in cfg), None)
    if not section or section not in cfg:
        sys.exit(f"no section {'[' + wanted + ']' if wanted else '/'.join(MYCNF_SECTIONS)} in {MYCNF}")
    s = cfg[section]
    return pymysql.connect(
        host=s.get("host", "127.0.0.1"),
        port=int(s.get("port", 3306)),
        user=s["user"],
        password=s.get("password", "").strip('"\''),
        database=DATABASE,
        connect_timeout=8,
        read_timeout=read_timeout,
        cursorclass=pymysql.cursors.DictCursor,
    )


def q(cur, sql, args=None):
    cur.execute(sql, args or None)      # None: no %-formatting, so LIKE '%' in literal SQL stays intact
    return cur.fetchall()


# ---------------------------------------------------------------- resolution

def resolve(cur, name):
    """Return the molecule_dictionary rows matching a name, synonym or ChEMBL ID (exact, case-insensitive)."""
    # UNION in a derived table instead of OR / IN(subquery): every branch uses its own index
    # (molecule_synonyms.synonyms needs x_idx_molsyn_synonyms from chembl_mech_build.py)
    return q(cur, """
        SELECT d.molregno, d.chembl_id, d.pref_name, d.max_phase, d.molecule_type
        FROM (SELECT molregno FROM molecule_dictionary WHERE pref_name = %s
              UNION SELECT molregno FROM molecule_dictionary WHERE chembl_id = %s
              UNION SELECT molregno FROM molecule_synonyms WHERE synonyms = %s) u
        JOIN molecule_dictionary d ON d.molregno = u.molregno
        ORDER BY d.max_phase DESC, d.molregno""", (name, name.upper(), name))


def search(cur, prefix, limit=40):
    """Prefix search over names and synonyms, for --search."""
    return q(cur, """
        SELECT d.molregno, d.chembl_id, d.pref_name, d.max_phase, d.molecule_type,
               (SELECT GROUP_CONCAT(DISTINCT s.synonyms ORDER BY s.synonyms SEPARATOR '; ')
                FROM molecule_synonyms s WHERE s.molregno = d.molregno) AS synonyms
        FROM (SELECT molregno FROM molecule_dictionary WHERE pref_name LIKE %s
              UNION SELECT molregno FROM molecule_synonyms WHERE synonyms LIKE %s) u
        JOIN molecule_dictionary d ON d.molregno = u.molregno
        ORDER BY d.max_phase DESC, d.pref_name
        LIMIT %s""", (prefix + "%", prefix + "%", limit))


def family(cur, molregnos):
    """All molregnos sharing a parent with the given ones (salts, hydrates, ...)."""
    if not molregnos:
        return []
    ph = ",".join(["%s"] * len(molregnos))
    rows = q(cur, f"""
        SELECT DISTINCT h2.molregno
        FROM molecule_hierarchy h1
        JOIN molecule_hierarchy h2 ON h2.parent_molregno = h1.parent_molregno
        WHERE h1.molregno IN ({ph})""", tuple(molregnos))
    found = {r["molregno"] for r in rows} | set(molregnos)
    return sorted(found)


# ------------------------------------------------------------------ queries

def mechanisms(cur, molregnos):
    ph = ",".join(["%s"] * len(molregnos))
    return q(cur, f"""
        SELECT dm.mechanism_of_action, dm.action_type, dm.direct_interaction, dm.disease_efficacy,
               td.chembl_id AS target_chembl_id, td.pref_name AS target, td.organism, td.target_type,
               GROUP_CONCAT(DISTINCT cs.accession ORDER BY cs.accession SEPARATOR ',') AS uniprot
        FROM drug_mechanism dm
        LEFT JOIN target_dictionary td ON td.tid = dm.tid
        LEFT JOIN target_components tc ON tc.tid = td.tid
        LEFT JOIN component_sequences cs ON cs.component_id = tc.component_id
        WHERE dm.molregno IN ({ph})
        GROUP BY dm.mec_id
        ORDER BY td.organism, td.pref_name""", tuple(molregnos))


def activity_targets(cur, molregnos, human=False, min_n=1, min_pchembl=None, limit=None):
    ph = ",".join(["%s"] * len(molregnos))
    where = [f"a.molregno IN ({ph})"]
    args = list(molregnos)
    if human:
        where.append("td.organism = 'Homo sapiens'")
    having = ["COUNT(*) >= %s"]
    args.append(min_n)
    if min_pchembl is not None:
        having.append("MAX(a.pchembl_value) >= %s")
        args.append(min_pchembl)
    sql = f"""
        SELECT td.chembl_id AS target_chembl_id, td.pref_name AS target, td.organism, td.target_type,
               COUNT(*) AS n_act,
               SUM(a.pchembl_value IS NOT NULL) AS n_pchembl,
               MAX(a.pchembl_value) AS best_pchembl,
               ROUND(AVG(a.pchembl_value), 2) AS mean_pchembl,
               MAX(s.confidence_score) AS conf,
               GROUP_CONCAT(DISTINCT a.standard_type ORDER BY a.standard_type SEPARATOR ',') AS types,
               COUNT(DISTINCT s.doc_id) AS n_docs
        FROM activities a
        JOIN assays s ON s.assay_id = a.assay_id
        JOIN target_dictionary td ON td.tid = s.tid
        WHERE {' AND '.join(where)}
        GROUP BY td.tid
        HAVING {' AND '.join(having)}
        ORDER BY best_pchembl DESC, n_act DESC"""
    if limit:
        sql += " LIMIT %s"
        args.append(limit)
    return q(cur, sql, tuple(args))


def activity_detail(cur, molregnos, target_chembl_id):
    ph = ",".join(["%s"] * len(molregnos))
    return q(cur, f"""
        SELECT a.activity_id, d.chembl_id AS compound, a.standard_type, a.standard_relation,
               a.standard_value, a.standard_units, a.pchembl_value, a.activity_comment,
               s.assay_type, s.confidence_score AS conf, s.description AS assay,
               doc.journal, doc.year, doc.pubmed_id
        FROM activities a
        JOIN molecule_dictionary d ON d.molregno = a.molregno
        JOIN assays s ON s.assay_id = a.assay_id
        JOIN target_dictionary td ON td.tid = s.tid
        LEFT JOIN docs doc ON doc.doc_id = a.doc_id
        WHERE a.molregno IN ({ph}) AND td.chembl_id = %s
        ORDER BY a.pchembl_value DESC, a.standard_type, a.standard_value""",
             tuple(molregnos) + (target_chembl_id.upper(),))


# ------------------------------------------------------------ reverse: target

TARGET_COLS = ("td.tid, td.chembl_id AS target_chembl_id, td.pref_name AS target, td.organism, td.target_type, "
               "(SELECT GROUP_CONCAT(DISTINCT cs.accession ORDER BY cs.accession SEPARATOR ',') "
               " FROM target_components tc JOIN component_sequences cs ON cs.component_id = tc.component_id "
               " WHERE tc.tid = td.tid) AS uniprot")


def resolve_target(cur, name, organism=None, single_protein=True):
    """Targets matching a ChEMBL ID, exact target name, UniProt accession or gene symbol.

    A ChEMBL ID is returned as is. For the other forms single_protein=True keeps only SINGLE PROTEIN targets
    (otherwise every complex and protein family containing the gene is listed as well)."""
    rows = q(cur, f"SELECT {TARGET_COLS} FROM target_dictionary td WHERE td.chembl_id = %s", (name.upper(),))
    if rows:
        return rows
    where, args = [], [name, name, name]
    if organism:
        where.append("td.organism = %s")
        args.append(organism)
    if single_protein:
        where.append("td.target_type = 'SINGLE PROTEIN'")
    return q(cur, f"""
        SELECT {TARGET_COLS}
        FROM target_dictionary td
        WHERE td.tid IN (
            SELECT tid FROM target_dictionary WHERE pref_name = %s
            UNION SELECT tc.tid FROM component_sequences cs
                  JOIN target_components tc ON tc.component_id = cs.component_id WHERE cs.accession = %s
            UNION SELECT tc.tid FROM component_synonyms sy
                  JOIN target_components tc ON tc.component_id = sy.component_id
                  WHERE sy.component_synonym = %s AND sy.syn_type IN ('GENE_SYMBOL', 'GENE_SYMBOL_OTHER'))
        {('AND ' + ' AND '.join(where)) if where else ''}
        ORDER BY td.organism = 'Homo sapiens' DESC, td.organism, td.target_type""", tuple(args))


def search_targets(cur, prefix, organism=None, limit=50):
    """Prefix search over target names and gene symbols."""
    args = [prefix + "%", prefix + "%"]
    org = ""
    if organism:
        org = "AND td.organism = %s"
        args.append(organism)
    args.append(limit)
    return q(cur, f"""
        SELECT {TARGET_COLS},
               (SELECT GROUP_CONCAT(DISTINCT sy.component_synonym ORDER BY sy.component_synonym SEPARATOR ',')
                FROM target_components tc JOIN component_synonyms sy ON sy.component_id = tc.component_id
                WHERE tc.tid = td.tid AND sy.syn_type = 'GENE_SYMBOL') AS genes
        FROM target_dictionary td
        WHERE td.tid IN (
            SELECT tid FROM target_dictionary WHERE pref_name LIKE %s
            UNION SELECT tc.tid FROM component_synonyms sy
                  JOIN target_components tc ON tc.component_id = sy.component_id
                  WHERE sy.component_synonym LIKE %s AND sy.syn_type = 'GENE_SYMBOL')
        {org}
        ORDER BY td.organism = 'Homo sapiens' DESC, td.pref_name, td.organism
        LIMIT %s""", tuple(args))


def target_mechanisms(cur, tids):
    """Curated drugs acting on the targets (drug_mechanism)."""
    ph = ",".join(["%s"] * len(tids))
    return q(cur, f"""
        SELECT d.chembl_id AS compound_chembl_id, d.pref_name AS compound, d.max_phase,
               dm.mechanism_of_action, dm.action_type, td.chembl_id AS target_chembl_id, td.organism
        FROM drug_mechanism dm
        JOIN molecule_dictionary d ON d.molregno = dm.molregno
        JOIN target_dictionary td ON td.tid = dm.tid
        WHERE dm.tid IN ({ph})
        ORDER BY d.max_phase DESC, d.pref_name""", tuple(tids))


def target_compounds(cur, tids, min_n=1, min_pchembl=None, min_phase=None, named_only=False,
                     limit=200, offset=0, smiles=False):
    """Compounds with activity records on the targets, one row per compound, best pChEMBL first.

    Returns (rows, total) where total is the number of compounds before LIMIT/OFFSET."""
    ph = ",".join(["%s"] * len(tids))
    where = [f"s.tid IN ({ph})"]
    args = list(tids) + list(tids)          # once for the mechanism subquery, once for WHERE
    if min_pchembl is not None:
        where.append("a.pchembl_value >= %s")
        args.append(min_pchembl)
    if min_phase is not None:
        where.append("d.max_phase >= %s")
        args.append(min_phase)
    if named_only:
        where.append("d.pref_name IS NOT NULL")
    args.append(min_n)
    smi_col = ", cst.canonical_smiles AS smiles" if smiles else ""
    smi_join = "LEFT JOIN compound_structures cst ON cst.molregno = d.molregno" if smiles else ""
    sql = f"""
        SELECT SQL_CALC_FOUND_ROWS
               d.chembl_id AS compound_chembl_id, d.pref_name AS compound, d.max_phase, d.molecule_type,
               m.action_type AS mechanism,
               COUNT(*) AS n_act,
               SUM(a.pchembl_value IS NOT NULL) AS n_pchembl,
               MAX(a.pchembl_value) AS best_pchembl,
               ROUND(AVG(a.pchembl_value), 2) AS mean_pchembl,
               MAX(s.confidence_score) AS conf,
               GROUP_CONCAT(DISTINCT a.standard_type ORDER BY a.standard_type SEPARATOR ',') AS types,
               COUNT(DISTINCT s.doc_id) AS n_docs
               {smi_col}
        FROM assays s
        JOIN activities a ON a.assay_id = s.assay_id
        JOIN molecule_dictionary d ON d.molregno = a.molregno
        LEFT JOIN (SELECT molregno, GROUP_CONCAT(DISTINCT action_type SEPARATOR ',') AS action_type
                   FROM drug_mechanism WHERE tid IN ({ph}) GROUP BY molregno) m ON m.molregno = d.molregno
        {smi_join}
        WHERE {' AND '.join(where)}
        GROUP BY a.molregno
        HAVING COUNT(*) >= %s
        ORDER BY best_pchembl DESC, n_act DESC
        LIMIT %s OFFSET %s"""
    args += [limit, offset]
    rows = q(cur, sql, tuple(args))
    total = q(cur, "SELECT FOUND_ROWS() AS n")[0]["n"]
    return rows, total


# ------------------------------------------- evidence (x_mech_evidence view)

EVIDENCE_LEVELS = "ABCDP-"   # A curated, B/C/D measured on a molecular target, P phenotypic, '-' below pChEMBL 5


def _levels(levels):
    levels = [c for c in (levels or "ABCDP").upper() if c in EVIDENCE_LEVELS]
    if not levels:
        raise ValueError(f"evidence levels must be letters of {EVIDENCE_LEVELS}")
    return levels


EV_COLS = ("e.evidence, e.source, e.best_pchembl, e.mean_pchembl, e.n_meas, e.n_active, e.n_docs, e.n_docs_active, "
           "e.max_conf, e.first_year, e.last_year, e.types, e.mechanism_of_action, e.action_type")


def candidates_for_compound(cur, molregnos, levels="ABCDP", human=False, limit=None):
    """All targets of a compound with an evidence level, strongest first."""
    lv = _levels(levels)
    ph = ",".join(["%s"] * len(molregnos))
    args = list(molregnos) + lv
    org = ""
    if human:
        org = "AND td.organism = 'Homo sapiens'"
    sql = f"""
        SELECT td.chembl_id AS target_chembl_id, td.pref_name AS target, td.organism, td.target_type, {EV_COLS}
        FROM x_mech_evidence e
        JOIN target_dictionary td ON td.tid = e.tid
        WHERE e.molregno IN ({ph}) AND e.evidence IN ({','.join(['%s'] * len(lv))}) {org}
        ORDER BY e.evidence, e.best_pchembl DESC"""
    if limit:
        sql += " LIMIT %s"
        args.append(limit)
    return q(cur, sql, tuple(args))


def candidates_for_target(cur, tids, levels="ABCD", min_phase=None, named_only=False, limit=200, offset=0):
    """All compounds acting on the targets with an evidence level. Returns (rows, total)."""
    lv = _levels(levels)
    ph = ",".join(["%s"] * len(tids))
    where = [f"e.tid IN ({ph})", f"e.evidence IN ({','.join(['%s'] * len(lv))})"]
    args = list(tids) + lv
    if min_phase is not None:
        where.append("d.max_phase >= %s")
        args.append(min_phase)
    if named_only:
        where.append("d.pref_name IS NOT NULL")
    args += [limit, offset]
    rows = q(cur, f"""
        SELECT SQL_CALC_FOUND_ROWS
               d.chembl_id AS compound_chembl_id, d.pref_name AS compound, d.max_phase, d.molecule_type,
               td.chembl_id AS target_chembl_id, {EV_COLS}
        FROM x_mech_evidence e
        JOIN molecule_dictionary d ON d.molregno = e.molregno
        JOIN target_dictionary td ON td.tid = e.tid
        WHERE {' AND '.join(where)}
        ORDER BY e.evidence, e.best_pchembl DESC
        LIMIT %s OFFSET %s""", tuple(args))
    return rows, q(cur, "SELECT FOUND_ROWS() AS n")[0]["n"]


# --------------------------------------------------------------- indications

IND_COLS = """d.chembl_id AS compound_chembl_id, d.pref_name AS compound, d.max_phase AS max_phase_any,
              di.max_phase_for_ind AS phase_for_indication, di.efo_id, di.efo_term, di.mesh_id, di.mesh_heading,
              (SELECT GROUP_CONCAT(DISTINCT r.ref_type ORDER BY r.ref_type SEPARATOR ',')
               FROM indication_refs r WHERE r.drugind_id = di.drugind_id) AS ref_types,
              (SELECT COUNT(*) FROM indication_refs r WHERE r.drugind_id = di.drugind_id) AS n_refs,
              (SELECT GROUP_CONCAT(r.ref_url SEPARATOR ' ')
               FROM indication_refs r WHERE r.drugind_id = di.drugind_id) AS ref_urls"""


# ------------------------------------------------------------------ German disease words
# ChEMBL names diseases in English (EFO, MeSH). German words in a disease pattern are replaced by an English
# regular expression before the search, e.g. `Darmkrebs` -> colorectal/colon/rectal cancer|carcinoma|neoplasm.
# Keys are folded (lower case, ä->ae, ö->oe, ü->ue, ß->ss); only words that differ from the English term are listed.
_CANCER = r"(cancer|carcinoma|neoplasm|tumou?r|malignan)"
_ORGANS = {          # German organ stem -> English stem(s); combined with a cancer word: Lungenkrebs, Leberkarzinom
    "darm": "colo|colon|rect", "dickdarm": "colo|colon", "kolon": "colo|colon",
    "kolorektal": "colorectal", "kolorektales": "colorectal", "mastdarm": "rect", "enddarm": "rect|anal",
    "dünndarm": "small intestin|small bowel|duoden",
    "lunge": "lung|bronch|pulmonary", "lungen": "lung|bronch|pulmonary", "bronchial": "lung|bronch",
    "brust": "breast", "mamma": "breast", "prostata": "prostat", "magen": "gastric|stomach",
    "bauchspeicheldrüsen": "pancrea", "bauchspeicheldrüse": "pancrea", "pankreas": "pancrea",
    "leber": "liver|hepat", "leberzell": "hepatocellular|liver", "gallenblasen": "gallbladder|biliary",
    "gallengangs": "bile duct|biliary|cholangio", "haut": "skin|cutaneous",
    "gebärmutterhals": "cervi", "zervix": "cervi", "gebärmutter": "uter|endometri", "gebärmutterschleimhaut": "endometri",
    "eierstock": "ovar", "eierstocks": "ovar", "ovarial": "ovar", "nieren": "kidney|renal", "niere": "kidney|renal",
    "nierenzell": "renal cell", "blasen": "bladder|urothel", "harnblasen": "bladder|urothel",
    "hoden": "testi|germ cell", "schilddrüsen": "thyroid", "speiseröhren": "esophag|oesophag",
    "hirn": "brain|cerebr", "gehirn": "brain|cerebr", "knochen": "bone|osteo", "kehlkopf": "laryn",
    "mund": "oral|mouth", "rachen": "pharyn", "kopf-hals": "head and neck", "lymphdrüsen": "lymph",
}
_SUFFIXES = ("krebserkrankung", "krebs", "karzinome", "karzinom", "tumoren", "tumore", "tumor", "neoplasie")
_WORDS = {           # whole German words -> English regular expression
    "krebs": _CANCER, "tumor": _CANCER, "karzinom": "carcinoma", "metastasen": "metasta",
    "darmkrebs": r"(colo|colon|colonic|colorectal|rect|rectal|rectum) [a-z -]*" + _CANCER,
    "hautkrebs": r"melanoma|(skin|cutaneous|basal cell|squamous cell) [a-z -]*" + _CANCER,
    "schwarzer hautkrebs": "melanoma", "weisser hautkrebs": "basal cell|squamous cell carcinoma",
    "blutkrebs": "leuk|lymphoma|myeloma", "leukaemie": "leuk", "lymphdruesenkrebs": "lymphoma", "lymphom": "lymphoma",
    "hirntumor": r"glioma|glioblastoma|(brain|cerebr)[a-z -]*" + _CANCER, "glioblastom": "glioblastoma",
    "melanom": "melanoma", "sarkom": "sarcoma", "myelom": "myeloma", "plasmozytom": "myeloma",
    "bluthochdruck": "hypertension", "hypertonie": "hypertension", "herzinfarkt": "myocardial infarction",
    "herzinsuffizienz": "heart failure", "herzschwaeche": "heart failure", "vorhofflimmern": "atrial fibrillation",
    "schlaganfall": "stroke|cerebral infarction", "thrombose": "thrombo", "arteriosklerose": "atherosclero|arterioscler",
    "zuckerkrankheit": "diabetes", "fettleibigkeit": "obesity", "adipositas": "obesity", "gicht": "gout",
    "rheuma": "rheumat", "arthrose": "osteoarthritis", "osteoporose": "osteoporosis",
    "schuppenflechte": "psoriasis", "neurodermitis": "atopic dermatitis|eczema", "ekzem": "eczema|dermatitis",
    "morbus crohn": "crohn", "colitis ulcerosa": "ulcerative colitis", "reizdarm": "irritable bowel",
    "demenz": "dementia", "multiple sklerose": "multiple sclerosis", "epilepsie": "epilepsy", "migraene": "migraine",
    "schizophrenie": "schizophrenia", "depression": "depress", "angststoerung": "anxiety",
    "grippe": "influenza", "lungenentzuendung": "pneumonia", "tuberkulose": "tuberculosis",
    "blutarmut": "anemia|anaemia", "anaemie": "anemia|anaemia", "unfruchtbarkeit": "infertility",
    "nierenversagen": "kidney failure|renal failure|kidney disease", "leberzirrhose": "cirrhosis",
    "fettleber": "fatty liver|steatohepatitis|nafld|nash", "mukoviszidose": "cystic fibrosis",
}


def _fold(w):
    return w.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")


_ORGANS_F = {_fold(k): v for k, v in _ORGANS.items()}
_WORDS_F = {_fold(k): v for k, v in _WORDS.items()}
_TOKEN = re.compile(r"\\.|[^\W\d_]+(?:[ -][^\W\d_]+)?", re.UNICODE)


def _german_word(w):
    """English regular expression for one German word (or two-word phrase), or None."""
    f = _fold(w)
    if f in _WORDS_F:
        return _WORDS_F[f]
    for suf in _SUFFIXES:
        if f.endswith(suf) and len(f) > len(suf):
            stem = f[:-len(suf)].rstrip("-")
            for cand in (stem, stem[:-1] if stem.endswith(("s", "n", "e")) else None):
                if cand and cand in _ORGANS_F:
                    return f"({_ORGANS_F[cand]})[a-z -]*{_CANCER}"
    return None


def translate_german(pattern):
    """Replace German disease words in a pattern by English regular expressions.

    Returns (pattern, translated) with translated = [(german, english), ...]; escapes (`\\b`, `\\(`) are left
    alone. A two-word phrase (`multiple Sklerose`) is tried first, then each word on its own."""
    done = []

    def repl(m):
        tok = m.group(0)
        if tok.startswith("\\"):
            return tok
        en = _german_word(tok)
        if en:
            done.append((tok, en))
            return f"({en})"
        parts = re.split(r"([ -])", tok)
        if len(parts) == 3:
            return repl_one(parts[0]) + parts[1] + repl_one(parts[2])
        return tok

    def repl_one(w):
        en = _german_word(w)
        if en:
            done.append((w, en))
            return f"({en})"
        return w

    return _TOKEN.sub(repl, pattern), done


REGEX_ERROR = 1139          # MariaDB ER_REGEXP_ERROR: the pattern itself is invalid


def is_regex_error(exc):
    """True for a MariaDB error caused by an invalid regular expression (pymysql OperationalError 1139)."""
    return bool(exc.args) and exc.args[0] == REGEX_ERROR


def indications_for_disease(cur, pattern, min_phase=None):
    """All compounds with a drug_indication row for a disease.

    `pattern` is a regular expression (MariaDB REGEXP = PCRE, case-insensitive) matched against the EFO term
    and the MeSH heading: `lung` finds every term containing lung, `^lung` terms starting with it,
    `^lung cancer$` exactly that term, `lung|bronch` either word. An invalid pattern raises
    pymysql.err.OperationalError 1139, see is_regex_error().

    drug_indication lists every drug that reached a clinical trial for the indication, whatever the outcome."""
    args = [pattern, pattern]
    ph = ""
    if min_phase is not None:
        ph = "AND di.max_phase_for_ind >= %s"
        args.append(min_phase)
    return q(cur, f"""
        SELECT {IND_COLS}
        FROM drug_indication di JOIN molecule_dictionary d ON d.molregno = di.molregno
        WHERE (di.efo_term REGEXP %s OR di.mesh_heading REGEXP %s) {ph}
        ORDER BY di.max_phase_for_ind DESC, d.pref_name""", tuple(args))


def indication_terms(cur, pattern, limit=40):
    """Distinct disease terms matching the pattern, with the number of compounds and of approved drugs each.

    Lets a user see which EFO terms / MeSH headings a pattern hits before (or after) running the full query.
    Rows whose EFO term matches are counted under the EFO term, the others under the MeSH heading."""
    return q(cur, """
        SELECT term, kind, COUNT(DISTINCT molregno) AS n_compounds,
               COUNT(DISTINCT CASE WHEN ph >= 4 THEN molregno END) AS n_approved
        FROM (SELECT efo_term AS term, 'EFO' AS kind, molregno, max_phase_for_ind AS ph
              FROM drug_indication WHERE efo_term REGEXP %s
              UNION ALL
              SELECT mesh_heading, 'MeSH', molregno, max_phase_for_ind
              FROM drug_indication WHERE mesh_heading REGEXP %s AND NOT (efo_term REGEXP %s)) u
        WHERE term IS NOT NULL
        GROUP BY term, kind
        ORDER BY n_compounds DESC, term
        LIMIT %s""", (pattern, pattern, pattern, limit))


def indications_for_compound(cur, molregnos):
    ph = ",".join(["%s"] * len(molregnos))
    return q(cur, f"""
        SELECT {IND_COLS}
        FROM drug_indication di JOIN molecule_dictionary d ON d.molregno = di.molregno
        WHERE di.molregno IN ({ph})
        ORDER BY di.max_phase_for_ind DESC, di.efo_term""", tuple(molregnos))


# ------------------------------------------------------------------- output

def fmt(v):
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return str(int(v)) if v == v.to_integral() else f"{float(v):.4g}"
    return str(v)


def print_table(rows, cols, maxw=60):
    if not rows:
        print("  (none)")
        return
    data = [[fmt(r.get(c))[:maxw] for c in cols] for r in rows]
    widths = [max(len(c), *(len(d[i]) for d in data)) for i, c in enumerate(cols)]
    print("  " + "  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for d in data:
        print("  " + "  ".join(x.ljust(w) for x, w in zip(d, widths)))


def emit(rows, args, cols=None, title=None):
    if args.json:
        print(json.dumps(rows, default=fmt, indent=1, ensure_ascii=False))
    elif args.csv:
        if rows:
            w = csv.DictWriter(sys.stdout, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow({k: fmt(v) for k, v in r.items()})
    else:
        if title:
            print(f"\n{title} ({len(rows)})")
        print_table(rows, cols or (list(rows[0].keys()) if rows else []))


# --------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="compound name, synonym or ChEMBL ID")
    ap.add_argument("--search", action="store_true", help="prefix search, list matching compounds and exit")
    ap.add_argument("--exact", action="store_true", help="do not include salts/forms from molecule_hierarchy")
    ap.add_argument("--human", action="store_true", help="only Homo sapiens targets")
    ap.add_argument("--min-n", type=int, default=1, help="min. activity records per target (default 1)")
    ap.add_argument("--min-pchembl", type=float, help="min. best pChEMBL per target")
    ap.add_argument("--limit", type=int, help="max. number of activity targets")
    ap.add_argument("--detail", metavar="TARGET_CHEMBL_ID", help="list single activity records for this target")
    ap.add_argument("--no-mechanism", action="store_true", help="skip the mechanism-of-action section")
    ap.add_argument("--csv", action="store_true", help="CSV output (activity targets or --detail only)")
    ap.add_argument("--json", action="store_true", help="JSON output")
    rv = ap.add_argument_group("reverse: name is a target (ChEMBL ID, UniProt accession, gene symbol, target name)")
    rv.add_argument("--target", action="store_true", help="list the compounds acting on the target")
    rv.add_argument("--organism", help="e.g. 'Homo sapiens' (target resolution and --search)")
    rv.add_argument("--all-types", action="store_true", help="also complexes/families containing the gene")
    rv.add_argument("--min-phase", type=float, help="min. clinical phase of the compound (4 = approved)")
    rv.add_argument("--named", action="store_true", help="only compounds with a preferred name")
    rv.add_argument("--offset", type=int, default=0, help="skip the first N compounds")
    rv.add_argument("--smiles", action="store_true", help="add canonical SMILES")
    ev = ap.add_argument_group("evidence levels (needs chembl_mech_build.py)")
    ev.add_argument("--candidates", metavar="LEVELS", nargs="?", const="ABCDP",
                    help="list pairs from x_mech_evidence with these levels, e.g. ABC (default ABCDP); "
                         "A curated, B/C/D measured, P phenotypic, '-' below pChEMBL 5")
    args = ap.parse_args()

    db = connect()
    cur = db.cursor()

    if args.target:
        return main_target(cur, args)

    if args.search:
        rows = search(cur, args.name)
        emit(rows, args, ["chembl_id", "pref_name", "max_phase", "molecule_type", "synonyms"],
             f"compounds matching '{args.name}%'")
        return

    hits = resolve(cur, args.name)
    if not hits:
        cand = search(cur, args.name, 15)
        print(f"no exact match for '{args.name}'", file=sys.stderr)
        if cand:
            print("candidates:", file=sys.stderr)
            for c in cand:
                print(f"  {c['chembl_id']:14} {fmt(c['pref_name']):40} phase {fmt(c['max_phase'])}", file=sys.stderr)
        sys.exit(1)

    molregnos = [h["molregno"] for h in hits]
    if not args.exact:
        molregnos = family(cur, molregnos)
    ph = ",".join(["%s"] * len(molregnos))
    forms = q(cur, f"SELECT chembl_id, pref_name, max_phase, molecule_type FROM molecule_dictionary "
                   f"WHERE molregno IN ({ph}) ORDER BY max_phase DESC, chembl_id", tuple(molregnos))

    if args.candidates:
        rows = candidates_for_compound(cur, molregnos, args.candidates, args.human, args.limit)
        emit(rows, args, ["evidence", "target_chembl_id", "target", "organism", "target_type", "best_pchembl",
                          "n_meas", "n_docs_active", "max_conf", "last_year", "mechanism_of_action"],
             f"evidence for {args.name} (levels {args.candidates})")
        return

    if args.detail:
        rows = activity_detail(cur, molregnos, args.detail)
        emit(rows, args, ["compound", "standard_type", "standard_relation", "standard_value", "standard_units",
                          "pchembl_value", "conf", "assay", "journal", "year", "pubmed_id"],
             f"activities of {args.name} on {args.detail.upper()}")
        return

    if not (args.csv or args.json):
        print(f"compound: {args.name}  ->  " + ", ".join(
            f"{f['chembl_id']} {fmt(f['pref_name'])} (phase {fmt(f['max_phase'])})" for f in forms))

    if not args.no_mechanism and not (args.csv or args.json):
        emit(mechanisms(cur, molregnos), args,
             ["mechanism_of_action", "action_type", "target_chembl_id", "target", "organism", "target_type", "uniprot"],
             "mechanism of action (curated)")

    rows = activity_targets(cur, molregnos, args.human, args.min_n, args.min_pchembl, args.limit)
    emit(rows, args, ["target_chembl_id", "target", "organism", "target_type", "n_act", "n_pchembl",
                      "best_pchembl", "mean_pchembl", "conf", "n_docs", "types"],
         "measured targets (activities)")


def main_target(cur, args):
    if args.search:
        rows = search_targets(cur, args.name, args.organism)
        emit(rows, args, ["target_chembl_id", "target", "organism", "target_type", "uniprot", "genes"],
             f"targets matching '{args.name}%'")
        return

    targets = resolve_target(cur, args.name, args.organism, not args.all_types)
    if not targets:
        print(f"no target '{args.name}' (try --search, --all-types or another --organism)", file=sys.stderr)
        sys.exit(1)
    tids = [t["tid"] for t in targets]

    if args.candidates:
        if not (args.csv or args.json):
            emit(targets, args, ["target_chembl_id", "target", "organism", "target_type", "uniprot"], "target(s)")
        rows, total = candidates_for_target(cur, tids, args.candidates, args.min_phase, args.named,
                                            args.limit or 200, args.offset)
        emit(rows, args, ["evidence", "compound_chembl_id", "compound", "max_phase", "best_pchembl", "n_meas",
                          "n_docs_active", "max_conf", "last_year", "mechanism_of_action"],
             f"evidence (levels {args.candidates}, showing {len(rows)} of {total})")
        return

    if not (args.csv or args.json):
        emit(targets, args, ["target_chembl_id", "target", "organism", "target_type", "uniprot"], "target(s)")
        if not args.no_mechanism:
            emit(target_mechanisms(cur, tids), args,
                 ["compound_chembl_id", "compound", "max_phase", "mechanism_of_action", "action_type",
                  "target_chembl_id", "organism"], "drugs with curated mechanism on this target")

    rows, total = target_compounds(cur, tids, args.min_n, args.min_pchembl, args.min_phase, args.named,
                                   args.limit or 200, args.offset, args.smiles)
    cols = ["compound_chembl_id", "compound", "max_phase", "mechanism", "n_act", "n_pchembl", "best_pchembl",
            "mean_pchembl", "conf", "n_docs", "types"] + (["smiles"] if args.smiles else [])
    emit(rows, args, cols, f"compounds with activities (showing {len(rows)} of {total}, offset {args.offset})")


if __name__ == "__main__":
    main()
