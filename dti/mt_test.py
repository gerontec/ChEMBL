#!/usr/bin/env python3
"""Field trial 2: multitask target prediction for HUMAN targets, after the design of
github.com/chembl/chembl_multitask_model (EMBL-EBI, MIT), re-implemented against our MariaDB copy of ChEMBL 37.

Difference to trial 1 (dti_test.py): labels are active/inactive per target instead of a pChEMBL regression, so
measured inactives are used as such. The target is not a model input, it is an output head.

  step data      ChEMBL -> mt_pairs.tsv (molregno, target_chembl_id, label), mt_compounds.tsv (SMILES)
  step features  Morgan fingerprints, radius 2, 1024 bit -> mt_fps.npy, mt_rows.tsv
  step train     1024-2000-100-(1 per target), BCE per task, SGD lr 4.0, batch 32, 3 epochs, on the P4
  step blind     scopolamine (held out, with atropine and hyoscyamine) against all trained targets
  step base      baseline: highest Tanimoto similarity to a target's training actives (target fishing)

Criteria and measurements: CONCEPT.md, section "Trial 2". Data goes to DTI_DIR (default /mnt/archive/dti).
"""
import os, sys, time, json, argparse
import numpy as np

OUT = os.environ.get("DTI_DIR", "/mnt/archive/dti")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # chembl.py of this repo
T0 = time.time()
FP_SIZE, RADIUS = 1024, 2
HUMAN_TAX = 9606
MIN_ACTIVE = MIN_INACTIVE = 100
BLIND = ("SCOPOLAMINE", "ATROPINE", "HYOSCYAMINE")        # held out of training, never in any training batch
MUSCARINIC = "Muscarinic acetylcholine receptor M"        # pref_name prefix of the five blind-test targets


def log(msg):
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


# ---------------------------------------------------------------- data

QUERY = """
    SELECT a.doc_id, a.standard_value, mh.parent_molregno AS molregno,
           td.chembl_id AS target_chembl_id, pc.protein_class_desc
    FROM activities a
      JOIN assays s ON s.assay_id = a.assay_id
      JOIN target_dictionary td ON td.tid = s.tid
      JOIN target_components tc ON tc.tid = td.tid
      JOIN component_class cc ON cc.component_id = tc.component_id
      JOIN protein_classification pc ON pc.protein_class_id = cc.protein_class_id
      JOIN molecule_dictionary md ON md.molregno = a.molregno
      JOIN molecule_hierarchy mh ON mh.molregno = md.molregno
    WHERE a.standard_units = 'nM'
      AND a.standard_type IN ('EC50','IC50','Ki','Kd','XC50','AC50','Potency')
      AND a.data_validity_comment IS NULL
      AND a.standard_relation IN ('=','<')
      AND a.potential_duplicate = 0
      AND s.confidence_score >= 8
      AND td.target_type = 'SINGLE PROTEIN'
      AND td.tax_id = %s
"""


def set_active(value, protein_class):
    """Label as upstream set_active(): the rules are applied in order, the last matching one decides."""
    active = 0
    if value <= 1000:
        active = 1
    if "ion channel" in protein_class and value <= 10000:
        active = 1
    if "enzyme  kinase  protein kinase" in protein_class and value > 30:
        active = 0
    if "transcription factor  nuclear receptor" in protein_class and value > 100:
        active = 0
    if "membrane receptor  7tm" in protein_class and value > 100:
        active = 0
    return active


def step_data():
    import chembl
    import pymysql
    con = chembl.connect(read_timeout=7200)
    c = con.cursor()
    c.execute("SET SESSION max_statement_time = 0")
    log("query activities (human single proteins, confidence >= 8)")
    t = time.time()
    cs = con.cursor(pymysql.cursors.SSCursor)
    cs.execute(QUERY, (HUMAN_TAX,))
    best = {}                                    # (molregno, target) -> (standard_value, doc_id, protein_class)
    n = 0
    for doc_id, value, molregno, target, pclass in cs:
        n += 1
        if value is None:
            continue
        v = float(value)
        k = (int(molregno), target)
        old = best.get(k)
        if old is None or v < old[0]:
            best[k] = (v, doc_id, pclass or "")
    cs.close()
    log(f"{n} rows, {len(best)} compound/target pairs after keeping the lowest value per pair, {time.time() - t:.0f}s")

    act, inact, docs = {}, {}, {}
    pairs = []
    for (molregno, target), (v, doc_id, pclass) in best.items():
        label = set_active(v, pclass)
        pairs.append((molregno, target, label))
        (act if label else inact).setdefault(target, 0)
        if label:
            act[target] += 1
        else:
            inact[target] += 1
        docs.setdefault(target, set()).add(doc_id)
    keep = {t_ for t_ in docs
            if act.get(t_, 0) >= MIN_ACTIVE and inact.get(t_, 0) >= MIN_INACTIVE and len(docs[t_]) >= 2}
    log(f"{len(docs)} human targets measured, {len(keep)} kept (>= {MIN_ACTIVE} active, >= {MIN_INACTIVE} inactive, "
        f">= 2 documents)")

    pairs = [p for p in pairs if p[1] in keep]
    mols = sorted({p[0] for p in pairs})
    with open(f"{OUT}/mt_pairs.tsv", "w") as f:
        for molregno, target, label in sorted(pairs):
            f.write(f"{molregno}\t{target}\t{label}\n")
    n_act = sum(p[2] for p in pairs)
    med_a = int(np.median([act[t_] for t_ in keep]))
    med_i = int(np.median([inact[t_] for t_ in keep]))
    log(f"{len(pairs)} labels ({n_act} active, {len(pairs) - n_act} inactive), {len(mols)} compounds; "
        f"median per target: {med_a} active, {med_i} inactive")

    with open(f"{OUT}/mt_compounds.tsv", "w") as f:
        for i in range(0, len(mols), 5000):
            part = mols[i:i + 5000]
            c.execute("SELECT molregno, canonical_smiles FROM compound_structures WHERE molregno IN (" +
                      ",".join(["%s"] * len(part)) + ")", part)
            for r in c.fetchall():
                if r["canonical_smiles"]:
                    f.write(f"{r['molregno']}\t{r['canonical_smiles']}\n")
    log("SMILES written")

    write_meta(con)
    con.close()


def write_meta(con=None):
    """The five blind-test targets and the blind-test compounds, resolved from the database, not hardcoded.
    The whole salt family of each blind compound is held out of training, as in trial 1 (dti_test.toxin_molregnos)."""
    import chembl
    own = con is None
    if own:
        con = chembl.connect()
    c = con.cursor()
    c.execute("SELECT chembl_id, pref_name FROM target_dictionary WHERE pref_name LIKE %s AND tax_id = %s",
              (MUSCARINIC + "%", HUMAN_TAX))
    # the five receptors themselves, not the complexes and chimeras the LIKE also matches
    musc = {r["chembl_id"]: r["pref_name"] for r in c.fetchall()
            if r["pref_name"] in {MUSCARINIC + d for d in "12345"}}
    family, query = {}, {}
    for name in BLIND:
        hits = [h["molregno"] for h in chembl.resolve(c, name)]
        fam = sorted(int(m) for m in chembl.family(c, hits))
        family[name] = fam
        query[name] = int(hits[0]) if hits else None
    json.dump({"muscarinic": musc, "blind": query, "blind_family": family},
              open(f"{OUT}/mt_meta.json", "w"), indent=1)
    log(f"muscarinic targets: {sorted(musc.items(), key=lambda x: x[1])}")
    log("blind-test compounds: " + ", ".join(f"{n}: {query[n]} (family of {len(family[n])})" for n in BLIND))
    if own:
        con.close()


# ---------------------------------------------------------------- features

def step_features():
    from rdkit import Chem, DataStructs, RDLogger
    from rdkit.Chem import rdFingerprintGenerator
    RDLogger.DisableLog("rdApp.*")
    gen = rdFingerprintGenerator.GetMorganGenerator(radius=RADIUS, fpSize=FP_SIZE)
    t = time.time()
    rows, fps, bad = [], [], 0
    for line in open(f"{OUT}/mt_compounds.tsv"):
        molregno, smiles = line.rstrip("\n").split("\t")
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            bad += 1
            continue
        a = np.zeros((FP_SIZE,), dtype=np.uint8)
        DataStructs.ConvertToNumpyArray(gen.GetFingerprint(mol), a)
        rows.append(int(molregno))
        fps.append(a)
    fps = np.asarray(fps, dtype=np.uint8)
    np.save(f"{OUT}/mt_fps.npy", fps)
    with open(f"{OUT}/mt_rows.tsv", "w") as f:
        f.write("\n".join(str(m) for m in rows) + "\n")
    log(f"{len(rows)} fingerprints ({FP_SIZE} bit, r={RADIUS}), {bad} unreadable SMILES, {time.time() - t:.0f}s")


# ---------------------------------------------------------------- label matrix

def load():
    fps = np.load(f"{OUT}/mt_fps.npy")
    rows = [int(l) for l in open(f"{OUT}/mt_rows.tsv")]
    row_of = {m: i for i, m in enumerate(rows)}
    targets = sorted({l.split("\t")[1] for l in open(f"{OUT}/mt_pairs.tsv")})
    col_of = {t: j for j, t in enumerate(targets)}
    # sparse labels per compound row: lists of (column, label)
    cols = [[] for _ in rows]
    labs = [[] for _ in rows]
    for line in open(f"{OUT}/mt_pairs.tsv"):
        molregno, target, label = line.rstrip("\n").split("\t")
        i = row_of.get(int(molregno))
        if i is None:                                     # SMILES unreadable
            continue
        cols[i].append(col_of[target])
        labs[i].append(int(label))
    cols = [np.asarray(c, dtype=np.int32) for c in cols]
    labs = [np.asarray(l, dtype=np.float32) for l in labs]
    meta = json.load(open(f"{OUT}/mt_meta.json"))
    log(f"{len(rows)} compounds x {len(targets)} targets, {sum(len(c) for c in cols)} labels")
    return fps, rows, row_of, targets, col_of, cols, labs, meta


def blind_rows(meta, row_of):
    """one fingerprint row per blind-test compound: its own molregno, or another member of its salt family"""
    out = {}
    for name, molregno in meta["blind"].items():
        for m in [molregno] + meta["blind_family"].get(name, []):
            if m in row_of:
                out[name] = (m, row_of[m])
                break
    return out


def dense_batch(idx, cols, labs, n_t):
    """labels of the batch rows as a dense matrix, -1 where the pair was never measured."""
    y = np.full((len(idx), n_t), -1.0, dtype=np.float32)
    for r, i in enumerate(idx):
        y[r, cols[i]] = labs[i]
    return y


# ---------------------------------------------------------------- model

def build(n_t, device):
    import torch.nn as nn
    net = nn.Sequential(nn.Linear(FP_SIZE, 2000), nn.ReLU(), nn.Dropout(0.25),
                        nn.Linear(2000, 100), nn.ReLU(), nn.Linear(100, n_t), nn.Sigmoid())
    return net.to(device)


def metrics(y, p):
    """pooled over all valid labels, as upstream reports them"""
    from sklearn.metrics import roc_auc_score, matthews_corrcoef, confusion_matrix
    pred = (p >= 0.5).astype(int)
    tn, fp_, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    prec = tp / (tp + fp_) if tp + fp_ else 0.0
    sens = tp / (tp + fn) if tp + fn else 0.0
    return {"n": int(len(y)), "positives": int(y.sum()),
            "auc": float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else float("nan"),
            "mcc": float(matthews_corrcoef(y, pred)),
            "accuracy": float((pred == y).mean()), "sensitivity": float(sens),
            "specificity": float(tn / (tn + fp_)) if tn + fp_ else 0.0, "precision": float(prec),
            "f1": float(2 * prec * sens / (prec + sens)) if prec + sens else 0.0}


def step_train(seed=1, epochs=3, bs=32, lr=4.0):
    import torch
    fps, rows, row_of, targets, col_of, cols, labs, meta = load()
    n_t = len(targets)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log(f"device {device}, seed {seed}")

    # compound split: 10 % held out, the blind-test compounds always among them
    rng = np.random.default_rng(seed)
    n = len(rows)
    perm = rng.permutation(n)
    n_te = n // 10
    te = set(perm[:n_te].tolist())
    forced = sorted({row_of[m] for fam in meta["blind_family"].values() for m in fam if m in row_of})
    for i in forced:
        te.add(i)
    tr_idx = np.asarray([i for i in range(n) if i not in te and len(cols[i])], dtype=np.int64)
    te_idx = np.asarray([i for i in sorted(te) if len(cols[i])], dtype=np.int64)
    log(f"{len(tr_idx)} training compounds, {len(te_idx)} held-out compounds "
        f"(blind-test salt families held out: {len(forced)} compounds)")

    # task weights: 1 / number of labels of the task in the training set (upstream)
    cnt = np.zeros(n_t, dtype=np.float64)
    for i in tr_idx:
        np.add.at(cnt, cols[i], 1.0)
    w = torch.tensor(np.where(cnt > 0, 1.0 / np.maximum(cnt, 1), 0.0), dtype=torch.float32, device=device)

    torch.manual_seed(seed)
    net = build(n_t, device)
    opt = torch.optim.SGD(net.parameters(), lr=lr)
    X = torch.from_numpy(fps).to(torch.float32)

    for ep in range(epochs):
        net.train()
        t = time.time()
        order = rng.permutation(len(tr_idx))
        tot, steps = 0.0, 0
        for s in range(0, len(order), bs):
            idx = tr_idx[order[s:s + bs]]
            xb = X[idx].to(device, non_blocking=True)
            yb = torch.from_numpy(dense_batch(idx, cols, labs, n_t)).to(device)
            p = net(xb).clamp(1e-7, 1 - 1e-7)
            mask = (yb >= 0).to(torch.float32)
            bce = -(yb.clamp(min=0) * p.log() + (1 - yb.clamp(min=0)) * (1 - p).log()) * mask
            cntb = mask.sum(0)
            # one BCELoss(weight=w_task) per task with mean reduction over its valid labels, summed over tasks
            loss = (torch.where(cntb > 0, bce.sum(0) / cntb.clamp(min=1), torch.zeros_like(cntb)) * w).sum()
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += float(loss)
            steps += 1
        log(f"epoch {ep + 1}/{epochs}: loss {tot / steps:.6f}, {steps} steps, {time.time() - t:.0f}s")

    # held-out compounds, pooled over all their valid labels
    net.eval()
    ys, ps = [], []
    fset = set(forced)
    with torch.no_grad():
        for s in range(0, len(te_idx), 1000):
            idx = te_idx[s:s + 1000]
            if fset & set(idx.tolist()):
                idx = np.asarray([i for i in idx if i not in fset], dtype=np.int64)   # blind compound, not scored
                if not len(idx):
                    continue
            p = net(X[idx].to(device)).cpu().numpy()
            y = dense_batch(idx, cols, labs, n_t)
            m = y >= 0
            ys.append(y[m])
            ps.append(p[m])
    res = metrics(np.concatenate(ys), np.concatenate(ps))
    log("held-out compounds: " + json.dumps(res))

    torch.save({"state": net.state_dict(), "targets": targets, "seed": seed,
                "train_idx": tr_idx, "test_idx": te_idx}, f"{OUT}/mt_model_s{seed}.pt")
    json.dump(res, open(f"{OUT}/mt_metrics_s{seed}.json", "w"), indent=1)
    return res


def step_blind(seed=1, top_pct=1.0):
    import torch
    fps, rows, row_of, targets, col_of, cols, labs, meta = load()
    ck = torch.load(f"{OUT}/mt_model_s{seed}.pt", weights_only=False)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    net = build(len(ck["targets"]), device)
    net.load_state_dict(ck["state"])
    net.eval()
    targets = ck["targets"]
    musc = meta["muscarinic"]
    out = {}
    for name, (molregno, i) in blind_rows(meta, row_of).items():
        with torch.no_grad():
            p = net(torch.from_numpy(fps[i:i + 1]).to(torch.float32).to(device)).cpu().numpy()[0]
        order = np.argsort(-p)
        rank = {targets[j]: r + 1 for r, j in enumerate(order)}
        k = max(1, int(round(len(targets) * top_pct / 100)))
        hits = {musc[t_]: rank[t_] for t_ in musc if t_ in rank}
        out[name] = {"molregno": molregno, "n_targets": len(targets), "top_k": k,
                     "muscarinic_ranks": dict(sorted(hits.items(), key=lambda x: x[1])),
                     "in_top_k": sum(1 for r in hits.values() if r <= k),
                     "top": [(targets[j], round(float(p[j]), 4)) for j in order[:10]]}
        log(f"{name}: {out[name]['in_top_k']} of {len(hits)} muscarinic receptors in the top {k} of "
            f"{len(targets)}; ranks {list(out[name]['muscarinic_ranks'].values())}")
    json.dump(out, open(f"{OUT}/mt_blind_s{seed}.json", "w"), indent=1)
    return out


def step_base(seed=1, top_pct=1.0):
    """Baseline: a target's score for a compound is the highest Tanimoto similarity between the compound and the
    target's ACTIVE compounds in the training set (target fishing, SEA/SwissTargetPrediction in its simplest form)."""
    import torch
    fps, rows, row_of, targets, col_of, cols, labs, meta = load()
    ck = torch.load(f"{OUT}/mt_model_s{seed}.pt", weights_only=False)
    targets = ck["targets"]
    tr_idx = ck["train_idx"]
    n_t = len(targets)
    actives = [[] for _ in range(n_t)]
    for i in tr_idx:
        for j, l in zip(cols[i], labs[i]):
            if l > 0:
                actives[j].append(i)
    F = fps.astype(np.float32)
    pop = F.sum(1)
    out = {}
    for name, (molregno, i) in blind_rows(meta, row_of).items():
        q = F[i]
        inter = F @ q
        tani = inter / (pop + q.sum() - inter)
        score = np.zeros(n_t, dtype=np.float32)
        for j in range(n_t):
            if actives[j]:
                score[j] = tani[actives[j]].max()
        order = np.argsort(-score)
        rank = {targets[j]: r + 1 for r, j in enumerate(order)}
        musc = meta["muscarinic"]
        hits = {musc[t_]: rank[t_] for t_ in musc if t_ in rank}
        k = max(1, int(round(n_t * top_pct / 100)))
        out[name] = {"top_k": k, "muscarinic_ranks": dict(sorted(hits.items(), key=lambda x: x[1])),
                     "in_top_k": sum(1 for r in hits.values() if r <= k),
                     "top": [(targets[j], round(float(score[j]), 4)) for j in order[:10]]}
        log(f"baseline {name}: {out[name]['in_top_k']} of {len(hits)} in the top {k}; "
            f"ranks {list(out[name]['muscarinic_ranks'].values())}")
    json.dump(out, open(f"{OUT}/mt_base_s{seed}.json", "w"), indent=1)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["data", "meta", "features", "train", "blind", "base"])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=4.0)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if a.step == "data":
        step_data()
    elif a.step == "meta":
        write_meta()
    elif a.step == "features":
        step_features()
    elif a.step == "train":
        step_train(a.seed, a.epochs, a.batch_size, a.lr)
    elif a.step == "blind":
        step_blind(a.seed)
    else:
        step_base(a.seed)
    log("done")
