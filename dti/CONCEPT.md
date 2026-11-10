# Field trial: predicting the human targets of a compound

**Status: field trial, started 2026-11-10. It may fail.** The criteria below were written before the first
result; if the trial misses them it is stopped and this file records why. Nothing of it is part of the query
service until it passes.

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

(filled in after the run)
