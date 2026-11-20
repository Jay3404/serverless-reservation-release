# Offline Bind-prediction reproduction

Recompute the paper's Bind-prediction comparisons from the separately supplied
normalized prediction evidence. This is an offline analysis of measurements,
not a live Kubernetes execution. The scorer uses Python 3.10+ and the standard
library; ReportLab is required only when generating vector figures.

```sh
# Run from the repository root; the output path must not already exist.
python3 -B code/prediction/reproduce.py \
  --evidence /path/to/evidence/prediction --out /path/to/new/prediction-results
```

The external `prediction/` directory contains a hash manifest and
pseudonymized relative-time records. It includes separate calibration and
held-out observations, frozen predictions to compare against fresh
calculations, and the observations needed for the two historical 80-trial
pages of analysis. A flat ZIP with the same files is also accepted. Input is
never edited.

The run writes `prediction_report.json` (comparison statistics and
diagnostics), `predictions96.csv`, `boundary_gains.csv`, and
`historical_predictions.csv`. Its calculations include:

- both 80-trial historical campaigns (40 contended trials each), including
  the ordinary-overhead medians and retrospective 10-trial fixed fit;
- both 56- and 96-trial prediction campaign results, with calibration terms,
  fixed vs. signal comparisons, per-session and duration summaries;
- post-hoc cache-rejection and endpoint sensitivity, cutoff reservation and
  clock descriptions, and the calibration-only fixed-return optimum.

Add `--figures` to generate Figures 1–4 in `figures/`. Figures 1 and 2
are schematics. Figure 3 uses all 36 paired boundary losses; Figure 4 uses
all 288 absolute errors of the 96 trials. PDF bytes can differ by renderer
and fonts; verify numeric chart inputs through the output CSV files. For
English DejaVu display, optionally give `--font-dir` pointing at the regular
and bold DejaVu Sans TTFs.

To reproduce Figure 3 using the paper's original layout rather than the
portable renderer, install Matplotlib. For the Korean version also use a
locally installed UnDotum font (not distributed with this repository):

```sh
python3 -B code/figures/plot_boundary_paper.py \
  --gains /path/to/prediction-results/boundary_gains.csv \
  --language ko --korean-font /path/to/UnDotum.ttf \
  --output /path/to/new/boundary-gain-ko.pdf
```

`prepare_evidence.py` exports these minimal inputs from the original authorized
research tree and its separate normalized review-archive ZIP. It verifies
recorded hashes and exports relative times and small comparison fields, not
raw cluster files or original resource IDs. It is for maintainers who have
the original inputs, not a downloader or replacement for the supplied evidence.

## Earlier review archive (56/96 only)

Supply the separately obtained, authorized `fgcs_retirement_review_evidence.zip`,
or an extracted `fgcs-evidence/` directory from that ZIP. This code does not
download it. The archived manifest is checked before scoring; even this
prediction-only subset checks **all** files indexed by that external manifest,
so provide the complete supplement rather than deleting its IBM CSVs. Those
CSVs are hash-checked only, not analyzed by this subset. Additional endpoint
files outside the original manifest are not covered by this check.

The ZIP inspected during packaging has SHA-256:

```text
842d1c6a5c5940500b0d304159018500eeafb2d0fdfe8415680cb50364e00ae0
```

The manifest is a byte-integrity check relative to supplied evidence, not a
digital signature or proof that underlying observations are correct. Verify the
ZIP fingerprint independently when using the specific archived release above.

## Run locally

From the repository root, replace the example external path:

```sh
python3 -B code/prediction/verify.py \
  --evidence /path/to/fgcs_retirement_review_evidence.zip

python3 -B code/prediction/verify.py \
  --evidence /path/to/fgcs-evidence --campaign 96 --json

python3 -B code/prediction/audit_boundary_rejections.py \
  --evidence /path/to/fgcs_retirement_review_evidence.zip

python3 -B -m unittest discover \
  -s code/prediction -p 'test_*.py' -v
```

Commands read external evidence and print to stdout; they never modify the
evidence or write an output file. Redirect stdout to a separate local output
directory if wanted. Do not use Python `-O`/`-OO`: original assertion-based
validation is deliberately retained, and the command refuses optimization.

## Preserved behavior and boundaries

- `verify.py` retains the archived `compute`, linear percentile, error summary,
  paired gains, calibration-median check and 56/96 campaign calculations.
  The original file's SHA-256 is
  `ac33e707f25788f57b4d61db896729004d1616e76698125a5273fb2c250c3717`.
  Changes are explicit external directory/ZIP input and removal of the separate
  IBM cell-analysis entry point. Archived frozen predictions are comparators,
  not inputs to forecast recomputation.
- Durations and event clocks are relative seconds. Forecasts are compared with
  the scheduler success-log Bind endpoint, using the archived 10 ns tolerance.
  Errors are milliseconds. Missing signal makes the hybrid use the fixed
  fallback; spare capacity preserves the archived no-contention branch.
- All 24 separate calibration trials per campaign and all 56/96 evaluation
  attempts remain. The 96-trial primary groups remain natural-boundary (36)
  and natural-early (12); pooled 48 depends on that assigned mix. Session,
  leave-one-session-out and duration summaries are descriptive, not new tests
  of independent observations or service-delay reductions.
- `audit_boundary_rejections.py` retains the archive's Decimal-based post-hoc
  diagnostic (original SHA-256
  `c2372adc4ee045dec7524488e657b5b2789ce8f9f4806bcd646323ec667d9a94`).
  Logged scheduler rejection only groups the same 36 boundary trials; it does
  not select rows, alter forecasts or replace the primary analysis.
- This is normalized-evidence rescoring, **not** reproduction of physical
  acquisition, cleanup instrumentation, scheduler cache behavior, endpoint
  audit acquisition, or an IBM trace simulation. It provides neither the
  restricted raw logs/identity mapping nor a complete experiment launcher.
  Supplement availability or these local checks imply no release permission,
  licensing decision, public deposit identifier, or independent measurement.

Synthetic tests exercise branch/fallback semantics, units, summaries and ZIP
integrity handling without bundling observations. Packaging validation against
the external archive compares both campaign dictionaries and the boundary
diagnostic with the original archived implementations; it does not recollect
or reinterpret the experiments.
