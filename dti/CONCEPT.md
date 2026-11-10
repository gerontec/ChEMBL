# Field trial: predicting the human targets of a compound

**Status: FAILED (run 1, 2026-11-10)** – criteria 1 and 2 missed, see *Results*. The criteria below were written
before the first result; the trial is stopped in this design. Nothing of it is part of the query service.

## Question

ChEMBL holds measured activities (pChEMBL) of about a million compounds on human proteins. For a compound
without measurements, for example a plant or animal toxin, the service can only say "no data". The trial asks:

> Can a model trained on the measured pairs rank the human proteins a **never-seen** compound acts on, well
> enough to point an expert at the right targets?

Only human targets: the use is medical (which human proteins a toxin or drug hits), not other species.

## Data

- ChEMBL 37 `activities` with `pchembl_value`, `standard_relation = '='`, assay `confidence_score = 9`
  (direct single protein), target type `SINGLE PROTEIN`, organism *Homo sapiens*
- several measurements of the same (compound, target) pair: mean pChEMBL
- all organisms: 2 010 095 pairs, 1 024 337 compounds, 7 309 targets; human: 4 579 targets (pairs counted in
  the run)

## Model

- compound: Morgan fingerprint (radius 2, 2048 bits, RDKit)
- protein: ESM-2 (35 M parameters, `facebook/esm2_t12_35M_UR50D`), mean over the residues of the target sequence
- two towers (fingerprint 2048 → 1024 → 256, protein 480 → 512 → 256), joint head 512 → 256 → 1, regression on
  pChEMBL, MSE, AdamW, 8 epochs
- hardware: Tesla P4 (8 GB, CUDA 11.8) on the database server; the camera and amyloid services were switched off
  for it

Code: `dti/dti_test.py` (steps `data`, `features`, `train`, `demo`).

## Evaluation

| split | what it tests | why |
|---|---|---|
| random pairs (90/10) | interpolation | optimistic: near-identical analogues of a test compound are in training |
| **held-out compounds** (10 % never seen, always incl. scopolamine, atropine, hyoscyamine) | **the question above** | a toxin without measurements is such a compound |
| held-out human targets (10 % never seen) | proteins without ChEMBL data | secondary |

Baselines: on held-out compounds the target's mean pChEMBL in training (a model that knows nothing about the
compound); on held-out targets the compound's mean pChEMBL.

**Blind test**: scopolamine (never in training) is scored against all human targets; the five muscarinic
acetylcholine receptors are its known targets.

## Pass / fail (fixed before the first result)

The trial passes only if **all** hold:

1. held-out compounds: RMSE at least 10 % below the target-mean baseline, and Pearson r ≥ 0.5
2. blind test: at least three of the five muscarinic receptors among the top 1 % of all human targets
   (top 46 of 4 579)
3. the result holds on a second run with another random seed

Otherwise the trial has failed for this design. Possible next designs (each a new trial with its own criteria):
scaffold split, larger ESM-2, graph network on the molecule (Chemprop), inactive data from `activities` with
`standard_relation = '>'`.

## Known pitfalls

- **Analogue leakage**: a random compound split still leaves close analogues of a test compound in training;
  the held-out-compound score is therefore optimistic. A scaffold split would be stricter.
- **Missing negatives**: inactive compounds are rarely reported with a pChEMBL value; the model learns from
  active pairs and tends to predict activity everywhere. Ranking (blind test) matters more than the absolute value.
- **Measurement noise**: the same pair differs by about 0.5 pChEMBL between laboratories; an RMSE much below that
  is not reachable.
- **Bias** towards well-studied target families (kinases, GPCRs): predictions for them are better than for
  rarely studied proteins.

## If it passes

Predictions are shown separately from measured data, never mixed: a column "predicted (model)" in the
ChEMBL ↔ nr form and an API field `predicted_pchembl`, with the trial's scores next to it. They are hypotheses for
experts, not evidence; the evidence layer (`x_mech_evidence`, levels A–D) stays measured data only.

## Results

### Run 1 (2026-11-10, seed 1) – failed

Steps and measurements (Tesla P4, 20 CPU threads; log in `DTI_DIR/run.log`):

| step | measured |
|---|---|
| data: ChEMBL query (all organisms) | 2 010 095 pairs, 1 024 337 compounds, 7 309 targets, 74 s; SMILES and sequences 83 s |
| features: Morgan fingerprints | 1 023 345 compounds, 0 unreadable SMILES, 137 s (one CPU thread) |
| features: ESM-2 embeddings | 4 579 human targets, 124 s on the P4 (fp16) |
| training data, human only | 1 555 744 pairs, 876 824 compounds, 4 578 targets; pChEMBL mean 6.13, sd 1.43 |
| training | 8 epochs per split, ~9 s per epoch; the P4 was mostly idle (batches assembled on one CPU thread) |

Scores (RMSE and r against measured pChEMBL; "within 1" = error at most a factor 10 in concentration):

| split | RMSE | r | within 1 | baseline | baseline RMSE | baseline r |
|---|---:|---:|---:|---|---:|---:|
| random pairs | 0.791 | 0.836 | 82 % | – | | |
| **held-out compounds** (87 686 compounds, 154 837 pairs) | **0.913** | 0.831 | 76 % | target's mean pChEMBL | 0.985 | 0.723 |
| held-out targets (457 targets, 129 953 pairs) | 1.252 | 0.461 | 59 % | compound's mean pChEMBL | 1.328 | 0.421 |

Blind test, scopolamine (never in training), all 4 579 human targets ranked by predicted pChEMBL:

- ranks of the five muscarinic receptors: **340, 428, 510, 697, 1079**
- top of the list: mucin-1, ATP synthase F(0) subunit 8, nicotinate phosphoribosyltransferase, cyclin-T1,
  cyclin-T2, NADH-ubiquinone oxidoreductase chain 4 – no relation to scopolamine

Against the criteria:

1. held-out compounds: RMSE **7.3 %** below the baseline (required: 10 %); r 0.83 (required: 0.5) – **missed**.
   Epoch 6 had RMSE 0.816 (17 % below); the criterion fixes 8 epochs, picking the best epoch afterwards would be
   tuning on the test set and does not count.
2. blind test: 0 of 5 muscarinic receptors in the top 1 % (top 46) – **missed clearly**.
3. second seed: not run, 1 and 2 already failed.

Why it fails: the model mostly learned **how potent the compounds measured on a target usually are**, not whether
a given compound fits that target. Targets with few, very potent measured compounds (mucin-1, ATP synthase
subunit 8) come out on top for any compound. ChEMBL holds almost only active pairs with a pChEMBL value; without
inactive pairs a regression on pChEMBL cannot rank targets for one compound (the "missing negatives" pitfall).
The good per-pair scores (r 0.83 on new compounds) come from the same effect: the baseline "target mean" alone
reaches r 0.72.

### Prior work (added after run 1)

Target prediction from ChEMBL is an established field; this trial is not new in method. Known approaches that a
next design would have to beat on the same blind test, not only the target-mean baseline:

- ligand similarity ("target fishing"): SEA (Keiser et al. 2007/2009), SwissTargetPrediction, PPB2 – nearest
  neighbours by Tanimoto among the measured ligands of each target; simple and often hard to beat on new compounds
- ChEMBL's own multitask target-prediction models (EMBL-EBI, published as ONNX), trained on ChEMBL with
  active/inactive labels per target
- pair models with sequence and molecule: DeepDTA, GraphDTA, MolTrans, DeepPurpose, ConPLex (protein language
  model, contrastive training with decoys)

A next trial (new criteria before the first result): classification active/inactive per target with negatives
(measured inactives, `standard_relation = '>'`, plus decoys), ranking metric on held-out compounds, the similarity
search and the ChEMBL multitask models as baselines.
