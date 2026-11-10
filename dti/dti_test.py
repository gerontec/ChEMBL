#!/usr/bin/env python3
"""Feasibility test: predict compound activity (pChEMBL) on HUMAN proteins from a compound fingerprint and a protein
sequence embedding, trained on the local ChEMBL (human single-protein targets only). The question is whether it
finds the human targets of a compound it has never seen (a toxin without measurements).

  step data     ChEMBL -> pairs.tsv (molregno, tid, mean pChEMBL, n), compounds.tsv (SMILES), targets.tsv (sequence)
  step features Morgan fingerprints (2048 bit, r=2), ESM-2 35M mean embeddings of the target sequences
  step train    MLP on the P4, three splits: random pairs; held-out compounds (10 % never seen, always including
                scopolamine, atropine, hyoscyamine); held-out human targets (10 % never seen)
  step demo     blind test: rank all human targets for scopolamine with the held-out-compound model

Needs torch with CUDA, rdkit and transformers (ESM-2 weights come from the Hugging Face hub).
Data and models go to DTI_DIR (default /mnt/archive/dti). Concept and pass/fail criteria: CONCEPT.md.
"""
import os, sys, time, json, math, argparse
import numpy as np

OUT = os.environ.get("DTI_DIR", "/mnt/archive/dti")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # chembl.py of this repo
T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


def step_data():
    import chembl
    import pymysql
    con = chembl.connect(read_timeout=3600)
    c = con.cursor()
    c.execute("SET SESSION max_statement_time = 0")
    log("query pairs (pChEMBL, '=', SINGLE PROTEIN, confidence 9)")
    cs = con.cursor(pymysql.cursors.SSCursor)
    cs.execute("""
        SELECT a.molregno, s.tid, AVG(a.pchembl_value), COUNT(*)
        FROM activities a
        JOIN assays s ON s.assay_id = a.assay_id
        JOIN target_dictionary td ON td.tid = s.tid
        WHERE a.pchembl_value IS NOT NULL AND a.standard_relation = '=' AND s.confidence_score = 9
          AND td.target_type = 'SINGLE PROTEIN'
        GROUP BY a.molregno, s.tid""")
    n = 0
    with open(f"{OUT}/pairs.tsv", "w") as f:
        for m, t, p, k in cs:
            f.write(f"{m}\t{t}\t{float(p):.3f}\t{k}\n")
            n += 1
    cs.close()
    log(f"{n} pairs")
    mols = sorted({int(l.split("\t")[0]) for l in open(f"{OUT}/pairs.tsv")})
    tids = sorted({int(l.split("\t")[1]) for l in open(f"{OUT}/pairs.tsv")})
    with open(f"{OUT}/compounds.tsv", "w") as f:
        for i in range(0, len(mols), 5000):
            part = mols[i:i + 5000]
            c.execute("SELECT molregno, canonical_smiles FROM compound_structures WHERE molregno IN (" +
                      ",".join(["%s"] * len(part)) + ")", part)
            for r in c.fetchall():
                if r["canonical_smiles"]:
                    f.write(f"{r['molregno']}\t{r['canonical_smiles']}\n")
    with open(f"{OUT}/targets.tsv", "w") as f:
        c.execute("SELECT td.tid, td.chembl_id, td.pref_name, td.organism, cs.accession, cs.sequence "
                  "FROM target_dictionary td JOIN target_components tc ON tc.tid = td.tid "
                  "JOIN component_sequences cs ON cs.component_id = tc.component_id "
                  "WHERE td.tid IN (" + ",".join(["%s"] * len(tids)) + ")", tids)
        for r in c.fetchall():
            if r["sequence"]:
                f.write(f"{r['tid']}\t{r['chembl_id']}\t{r['pref_name']}\t{r['organism']}\t{r['accession']}\t{r['sequence']}\n")
    log(f"{len(mols)} compounds, {len(tids)} targets written")


def morgan(smiles_list):
    from rdkit import Chem, RDLogger
    from rdkit.Chem import rdFingerprintGenerator
    RDLogger.DisableLog("rdApp.*")
    gen = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
    out = np.zeros((len(smiles_list), 2048 // 8), dtype=np.uint8)
    ok = np.zeros(len(smiles_list), dtype=bool)
    for i, s in enumerate(smiles_list):
        m = Chem.MolFromSmiles(s)
        if m is None:
            continue
        out[i] = np.packbits(gen.GetFingerprintAsNumPy(m).astype(np.uint8))
        ok[i] = True
    return out, ok


def esm_embed(seqs, batch=8):
    import torch
    from transformers import AutoTokenizer, AutoModel
    name = "facebook/esm2_t12_35M_UR50D"
    tok = AutoTokenizer.from_pretrained(name)
    model = AutoModel.from_pretrained(name).cuda().eval().half()
    out = np.zeros((len(seqs), model.config.hidden_size), dtype=np.float32)
    order = np.argsort([len(s) for s in seqs])
    with torch.no_grad():
        for b in range(0, len(seqs), batch):
            idx = order[b:b + batch]
            enc = tok([seqs[i][:1022] for i in idx], return_tensors="pt", padding=True).to("cuda")
            h = model(**enc).last_hidden_state.float()
            mask = enc["attention_mask"].unsqueeze(-1).float()
            out[idx] = ((h * mask).sum(1) / mask.sum(1)).cpu().numpy()
    return out


def step_features():
    if os.path.exists(f"{OUT}/compound_fp.npz"):
        log("fingerprints exist, kept")
    else:
        comp = [l.rstrip("\n").split("\t") for l in open(f"{OUT}/compounds.tsv")]
        log(f"Morgan fingerprints for {len(comp)} compounds")
        fp, ok = morgan([s for _, s in comp])
        ids = np.array([int(m) for m, _ in comp])[ok]
        np.savez(f"{OUT}/compound_fp.npz", molregno=ids, fp=fp[ok])
        log(f"{ok.sum()} fingerprints ({(~ok).sum()} SMILES unreadable)")
    tg = {}
    for l in open(f"{OUT}/targets.tsv"):
        tid, cid, name, org, acc, seq = l.rstrip("\n").split("\t")
        if org == "Homo sapiens":
            tg.setdefault(int(tid), seq)                   # SINGLE PROTEIN: one component
    tids = sorted(tg)
    log(f"ESM-2 embeddings for {len(tids)} human targets")
    emb = esm_embed([tg[t] for t in tids])
    np.savez(f"{OUT}/target_emb.npz", tid=np.array(tids), emb=emb)
    log("features done")


def load():
    f = np.load(f"{OUT}/compound_fp.npz")
    e = np.load(f"{OUT}/target_emb.npz")
    ci = {m: i for i, m in enumerate(f["molregno"])}
    ti = {t: i for i, t in enumerate(e["tid"])}
    P = np.loadtxt(f"{OUT}/pairs.tsv", dtype=np.float64, ndmin=2)
    keep = np.array([int(m) in ci and int(t) in ti for m, t in P[:, :2]])
    P = P[keep]
    cidx = np.array([ci[int(m)] for m in P[:, 0]])
    tidx = np.array([ti[int(t)] for t in P[:, 1]])
    return f["fp"], e["emb"], cidx, tidx, P[:, 2].astype(np.float32), e["tid"]


def build_model(d_prot):
    import torch.nn as nn

    class DTI(nn.Module):
        def __init__(self):
            super().__init__()
            self.c = nn.Sequential(nn.Linear(2048, 1024), nn.ReLU(), nn.Dropout(0.2), nn.Linear(1024, 256), nn.ReLU())
            self.p = nn.Sequential(nn.Linear(d_prot, 512), nn.ReLU(), nn.Dropout(0.2), nn.Linear(512, 256), nn.ReLU())
            self.h = nn.Sequential(nn.Linear(512, 256), nn.ReLU(), nn.Dropout(0.1), nn.Linear(256, 1))

        def forward(self, c, p):
            import torch
            return self.h(torch.cat([self.c(c), self.p(p)], 1)).squeeze(1)
    return DTI()


def fit(fp, emb, cidx, tidx, y, tr, te, epochs=8, bs=4096):
    import torch
    dev = "cuda"
    FP = torch.from_numpy(np.unpackbits(fp, axis=1).astype(np.float32))      # CPU; batches go to the GPU
    EM = torch.from_numpy(emb).to(dev)
    mu, sd = EM.mean(0), EM.std(0) + 1e-6
    EM = (EM - mu) / sd
    model = build_model(emb.shape[1]).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    Y = torch.from_numpy(y)
    for ep in range(epochs):
        model.train()
        perm = np.random.permutation(tr)
        tot = 0.0
        for b in range(0, len(perm), bs):
            i = perm[b:b + bs]
            c = FP[cidx[i]].to(dev, non_blocking=True)
            p = EM[tidx[i]]
            loss = torch.nn.functional.mse_loss(model(c, p), Y[i].to(dev))
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += loss.item() * len(i)
        pred = predict(model, FP, EM, cidx, tidx, te)
        log(f"  epoch {ep + 1}: train RMSE {math.sqrt(tot / len(perm)):.3f}, test RMSE "
            f"{np.sqrt(np.mean((pred - y[te]) ** 2)):.3f}, r {np.corrcoef(pred, y[te])[0, 1]:.3f}")
    return model, FP, EM, (mu, sd)


def predict(model, FP, EM, cidx, tidx, idx, bs=16384):
    import torch
    model.eval()
    out = []
    with torch.no_grad():
        for b in range(0, len(idx), bs):
            i = idx[b:b + bs]
            out.append(model(FP[cidx[i]].to("cuda"), EM[tidx[i]]).float().cpu().numpy())
    return np.concatenate(out) if out else np.zeros(0)


def report(name, pred, y):
    rmse = float(np.sqrt(np.mean((pred - y) ** 2)))
    r = float(np.corrcoef(pred, y)[0, 1])
    within1 = float(np.mean(np.abs(pred - y) <= 1.0))
    log(f"{name}: RMSE {rmse:.3f} pChEMBL units, r {r:.3f}, |error| <= 1 (factor 10) for {within1 * 100:.0f} %")
    return {"rmse": rmse, "r": r, "within_1": within1}


def toxin_molregnos():
    import chembl
    c = chembl.connect().cursor()
    out = set()
    for n in ("SCOPOLAMINE", "ATROPINE", "HYOSCYAMINE"):
        out |= set(chembl.family(c, [h["molregno"] for h in chembl.resolve(c, n)]))
    return out


def step_train():
    import torch
    rng = np.random.default_rng(1)
    fp, emb, cidx, tidx, y, tid_list = load()      # load() keeps only targets with an embedding = human
    mol_list = np.load(f"{OUT}/compound_fp.npz")["molregno"]
    log(f"{len(y)} human pairs, {len(set(cidx))} compounds, {len(set(tidx))} human targets; "
        f"pChEMBL mean {y.mean():.2f} sd {y.std():.2f}")
    res = {"n_pairs": int(len(y)), "n_targets": int(len(set(tidx))), "pchembl_sd": float(y.std())}

    log("split 1: random 90/10 pairs")
    perm = rng.permutation(len(y))
    te, tr = perm[:len(y) // 10], perm[len(y) // 10:]
    model, FP, EM, _ = fit(fp, emb, cidx, tidx, y, tr, te)
    res["random"] = report("random split", predict(model, FP, EM, cidx, tidx, te), y[te])

    tox = toxin_molregnos()
    uc = np.unique(cidx)
    held = set(rng.choice(uc, len(uc) // 10, replace=False).tolist()) | \
        {i for i, m in enumerate(mol_list) if int(m) in tox}
    mask = np.array([c in held for c in cidx])
    te, tr = np.flatnonzero(mask), np.flatnonzero(~mask)
    log(f"split 2: {len(held)} held-out compounds (incl. {len(tox)} forms of scopolamine/atropine/hyoscyamine), "
        f"{len(te)} test pairs")
    model2, FP, EM, norm = fit(fp, emb, cidx, tidx, y, tr, te)
    res["new_compounds"] = report("held-out compounds", predict(model2, FP, EM, cidx, tidx, te), y[te])
    tm = {}
    for i in tr:
        tm.setdefault(tidx[i], []).append(y[i])
    base = np.array([np.mean(tm[t]) if t in tm else y[tr].mean() for t in tidx[te]])
    res["baseline_target_mean"] = report("baseline (target's mean pChEMBL)", base, y[te])
    torch.save({"model": model2.state_dict(), "norm": [t.cpu() for t in norm]}, f"{OUT}/dti_new_compound.pt")

    ut = np.unique(tidx)
    held_t = set(rng.choice(ut, len(ut) // 10, replace=False).tolist())
    mask = np.array([t in held_t for t in tidx])
    te, tr = np.flatnonzero(mask), np.flatnonzero(~mask)
    log(f"split 3: {len(held_t)} held-out human targets, {len(te)} test pairs")
    model3, FP, EM, _ = fit(fp, emb, cidx, tidx, y, tr, te)
    res["new_targets"] = report("held-out targets", predict(model3, FP, EM, cidx, tidx, te), y[te])
    cm = {}
    for i in tr:
        cm.setdefault(cidx[i], []).append(y[i])
    base = np.array([np.mean(cm[c]) if c in cm else y[tr].mean() for c in cidx[te]])
    res["baseline_compound_mean"] = report("baseline (compound's mean pChEMBL)", base, y[te])
    json.dump(res, open(f"{OUT}/results.json", "w"), indent=1)
    log("train done")


def step_demo():
    """Blind test: scopolamine was never seen in training (held-out compounds). Rank every human target."""
    import torch
    import chembl
    fp, emb, cidx, tidx, y, tid_list = load()
    ck = torch.load(f"{OUT}/dti_new_compound.pt")
    model = build_model(emb.shape[1]).cuda()
    model.load_state_dict(ck["model"])
    model.eval()
    mu, sd = [t.cuda() for t in ck["norm"]]
    c = chembl.connect().cursor()
    m = [h["molregno"] for h in chembl.resolve(c, "SCOPOLAMINE")]
    smi = chembl.q(c, "SELECT canonical_smiles FROM compound_structures WHERE molregno = %s", (m[0],))[0]["canonical_smiles"]
    cfp, _ = morgan([smi])
    cfp = torch.from_numpy(np.unpackbits(cfp, axis=1).astype(np.float32)).cuda()
    E = (torch.from_numpy(emb).cuda() - mu) / sd
    with torch.no_grad():
        pred = model(cfp.repeat(len(E), 1), E).cpu().numpy()
    tg = {int(l.split("\t")[0]): l.rstrip("\n").split("\t") for l in open(f"{OUT}/targets.tsv")}
    tox = toxin_molregnos()
    measured = {}
    for l in open(f"{OUT}/pairs.tsv"):
        mm, t, p, k = l.split("\t")
        if int(mm) in tox:
            measured.setdefault(int(t), []).append(float(p))
    order = np.argsort(-pred)
    log(f"scopolamine (never seen in training): top 20 of {len(pred)} human targets")
    for rank, i in enumerate(order[:20], 1):
        t = int(tid_list[i])
        meas = measured.get(t)
        print(f"   {rank:3d}. {pred[i]:5.2f}  {tg[t][2][:55]:55s} {tg[t][4]:8s} "
              f"measured (scopolamine/atropine/hyoscyamine): {max(meas) if meas else '–'}", flush=True)
    musc = [i for i in range(len(pred)) if "muscarinic" in tg[int(tid_list[i])][2].lower()]
    ranks = sorted(int(np.flatnonzero(order == i)[0]) + 1 for i in musc)
    log(f"ranks of the {len(musc)} muscarinic receptors among {len(pred)} human targets: {ranks}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("steps", nargs="+", choices=["data", "features", "train", "demo"])
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    for s in a.steps:
        log(f"=== {s} ===")
        globals()["step_" + s]()
