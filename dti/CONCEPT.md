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

## Trial 2: ChEMBL's own multitask design (criteria fixed 2026-11-10, before the first result)

Trial 1 failed because ChEMBL's pChEMBL rows are almost only actives, so a regression on pChEMBL learns the
potency level of a target instead of the fit of a compound. Trial 2 changes exactly that one thing: **labels are
active/inactive per target**, and inactive measurements are used as such.

The design is taken from `github.com/chembl/chembl_multitask_model` (EMBL-EBI, MIT licence), which is the
reference implementation of this approach. Our scripts are a re-implementation of its design against our own
MariaDB copy of ChEMBL 37, not a run of its code: its pipeline reads a ChEMBL SQLite dump through SQLAlchemy and
trains with PyTorch Lightning and PyTables. Numbers published for their models are therefore not a result of
ours; where they differ, the difference may be ours.

### Data (one change against upstream: human only)

Same query as upstream (`activities` × `assays` × `target_dictionary` × `target_components` × `component_class` ×
`protein_classification` × `molecule_dictionary` × `molecule_hierarchy` × `compound_structures`), same filters:

- `standard_units = 'nM'`, `standard_type IN ('EC50','IC50','Ki','Kd','XC50','AC50','Potency')`
- `data_validity_comment IS NULL`, `standard_relation IN ('=','<')`, `potential_duplicate = 0`
- `assays.confidence_score >= 8` (the assay is assigned to a single protein with confidence)
- `target_dictionary.target_type = 'SINGLE PROTEIN'`
- **added: `target_dictionary.tax_id = 9606`** — the trial only looks for human targets
- one row per (parent compound, target), the one with the lowest `standard_value`

Labels (upstream `set_active`, thresholds in nM on `standard_value`):

| target class | active | inactive |
|---|---|---|
| default | ≤ 1000 | > 1000 |
| ion channel | ≤ 10000 | > 10000 |
| protein kinase | ≤ 30 | > 30 |
| nuclear receptor, 7TM receptor | ≤ 100 | > 100 |

The class rules are applied in this order and the last matching one decides, so a target in several classes is
labelled by the narrowest threshold — as upstream does it.

A target is kept with **at least 100 active and 100 inactive compounds** and measurements from **at least two
documents**. Everything else is dropped. The training matrix is compound × target and sparse: a pair that was
never measured carries the label −1 and is masked out of the loss, it is not a negative.

### Features and model (as upstream)

Morgan fingerprint, radius 2, **1024 bits** (trial 1 used 2048 and an ESM-2 embedding of the protein; here the
target is not a model input at all, it is an output head).

`1024 → 2000` ReLU, dropout 0.25 → `100` ReLU → one `100 → 1` sigmoid head per target. One `BCELoss` per target,
weighted by the inverse of the target's number of labels, missing labels masked out, the per-target losses summed.
SGD, learning rate 4.0 (large because of that weighting), batch size 32, 3 epochs. Tesla P4, CUDA 11.8.

### Evaluation

- **held-out compounds**: 10 % of the compounds, drawn by compound, never seen in training. Metrics pooled over
  all valid labels of those compounds: ROC AUC, MCC, accuracy, sensitivity, specificity, F1 (upstream's metrics).
- **blind test, unchanged from trial 1**: scopolamine, atropine and hyoscyamine are forced into the held-out set,
  so no close analogue of scopolamine is in training. Scopolamine is scored against all trained human targets and
  the ranks of the five muscarinic acetylcholine receptors (M1–M5, CHEMBL216/211/245/1821/1836) are read off.
- **baseline, new**: target fishing by similarity. A target's score for a compound is the highest Tanimoto
  similarity between that compound and the target's active compounds in training (same fingerprints). This is the
  method of SEA and SwissTargetPrediction in its simplest form, and it is the baseline the trial has to beat —
  the target-mean baseline of trial 1 does not apply to classification.

### Pass / fail (fixed before the first result)

The trial passes only if **all** hold:

1. held-out compounds: ROC AUC ≥ 0.85 and MCC ≥ 0.5
2. blind test: at least three of the five muscarinic receptors among the **top 1 %** of the trained targets
3. the blind test is at least as good as the Tanimoto baseline (same count in the top 1 %, or more)
4. 1 to 3 hold again on a second run with another random seed

Criterion 3 is the point of the trial: a model that cannot beat a similarity search has no use next to one.
A pass on 1 and 2 but not 3 counts as failed and is recorded as such — the baseline then goes into the form
instead of the model.

### Measurements to record for every step

Rows and time of the query, compounds, targets and labels after filtering, actives/inactives per target
(median), fingerprint time, epochs and time per epoch, GPU utilisation, the metric table, the blind-test ranks
of M1–M5 with the top of the list, the same ranks for the baseline. Written into this file as a results section,
pass or fail.

### Trial 2, run 1 (2026-11-10, seed 1) – failed; the baseline passed the blind test

Steps and measurements (dell, Tesla P4, CUDA 11.8, env `iver`; log `DTI_DIR/mt_run.log`):

| step | measured |
|---|---|
| data: query (human, confidence ≥ 8, SINGLE PROTEIN) | 2 621 238 rows, 2 025 581 compound/target pairs after keeping the lowest value per pair, 43 s |
| data: target filter | 4 685 human targets measured, **643 kept** (≥ 100 active, ≥ 100 inactive, ≥ 2 documents) |
| data: labels | 1 767 417 labels (719 360 active, 1 048 057 inactive), 1 008 934 compounds; median per target 597 active / 504 inactive |
| data: SMILES | 1 007 469 compounds written, 83 s in total |
| features: Morgan 1024 bit, r = 2 | 1 007 469 fingerprints, **0 unreadable SMILES**, 165 s (one CPU thread) |
| split | 906 721 training compounds, 100 748 held-out compounds; the salt families of scopolamine, atropine and hyoscyamine (3 compounds with fingerprints) held out |
| training | 3 epochs × 28 336 steps, **34 s per epoch** on the P4; loss 0.0119 → 0.0097 → 0.0088 |
| blind test and baseline | 7 s / 12 s for three compounds against 643 targets |

Held-out compounds, pooled over 175 871 valid labels (71 720 of them active):

| AUC | MCC | accuracy | sensitivity | specificity | precision | F1 |
|---:|---:|---:|---:|---:|---:|---:|
| **0.909** | **0.660** | 0.837 | 0.779 | 0.876 | 0.812 | 0.796 |

Blind test: the three compounds against all 643 trained targets, top 1 % = top 6. Ranks of the five muscarinic
acetylcholine receptors (M1 CHEMBL216, M2 CHEMBL211, M3 CHEMBL245, M4 CHEMBL1821, M5 CHEMBL2035, all five are
among the 643):

| | scopolamine | atropine | hyoscyamine |
|---|---|---|---|
| multitask model | 83, 94, 100, 106, 142 → **0 of 5** | 58, 64, 76, 98, 135 → 0 of 5 | 58, 64, 76, 98, 135 → 0 of 5 |
| baseline (highest Tanimoto to a target's training actives) | **1, 3, 6, 7, 8 → 3 of 5** | 1, 2, 3, 4, 7 → 4 of 5 | 1, 2, 3, 4, 7 → 4 of 5 |

Against the criteria:

1. held-out compounds: AUC 0.909 (required ≥ 0.85), MCC 0.660 (required ≥ 0.5) – **met**
2. blind test: 0 of 5 muscarinic receptors in the top 6 – **missed**
3. at least as good as the Tanimoto baseline: the baseline has 3 of 5 in the top 6, the model 0 – **missed clearly**
4. second seed: not run, 2 and 3 already failed

**The trial has failed, and it failed in the way the criteria were written to catch.** The model is good at the
question "is this compound active on that target" when it is asked about one pair at a time (AUC 0.91 on
compounds it has never seen), and it is poor at the question the project actually has: "which of the 643 targets
belong to this compound". Per-pair quality and per-compound ranking are not the same measurement, and only the
second one is useful at the form.

Why: the per-target heads are trained independently and each one is calibrated to its own target. A target with
many potent, easy ligands produces high probabilities for almost any molecule, so the top of a per-compound
ranking is filled with such targets regardless of the molecule — the same effect that made trial 1 fail, now in
classification instead of regression. Trial 1 was wrong about the cause: adding inactives fixed the per-pair
score (trial 1's held-out r came from the target mean; here AUC 0.91 is real) but not the ranking across targets.
A ranking loss across the targets of one compound, or a calibration per compound, would be the next thing to
change — not more data.

**What is used instead**: the Tanimoto target fishing of the baseline. It needs no training, it is the method of
SEA and SwissTargetPrediction, and on this blind test it puts the right receptor family at ranks 1, 3 and 6 for a
compound whose whole salt family was removed from its reference set. Scores and ranks of trial 2 stay in this
file as the measurement it has to be compared against when it is put into the form.
