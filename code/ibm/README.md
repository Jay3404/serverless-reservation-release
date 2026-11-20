# IBM closed-loop replay

This directory contains the unchanged event kernel and controller, the archived
TTL sweep tools, and offline audits for the IBM Code Engine comparisons. The
scripts do not provision a cluster or acquire new timing measurements. A
separate, authorized evidence snapshot supplies the normalized IBM rows and
Kind timing inputs; this code directory contains no experiment data.

## Test without data

Run from the repository root with Python 3.10+ on a POSIX system (Linux/macOS). No
third-party Python packages, network access or GPU are needed.

```sh
python3 -B code/ibm/test_run.py
```

The deterministic fixtures check signal eligibility, the 1-second decision
horizon, physical reservation accounting, and how forecasts affect actual
retirement and placement. They do not reproduce the paper's workload results.

## Assemble the frozen replay input

The runner reads `inputs.json.gz` of schema `IBM_LIFECYCLE_PORTABLE_INPUT_V1`.
The normalized snapshot contains the selected request events and the separate
Kind calibration. The assembly command checks the historical window bounds,
counts, and full uncompressed SHA-256. On a matching zlib implementation it
also reproduces the historical compressed bytes. The public raw trace alone
does not contain the measured timing or the frozen reference selection.

```bash
mkdir -p work
python3 -B code/ibm/prepare.py build /path/to/evidence/ibm/normalized work/inputs.json.gz
```

To inspect the normalized inputs, read `metadata.json` and the three window
files (`train.json.gz`, `day1.json.gz`, `day2.json.gz`). The inverse
`prepare.py split` command operates on an original frozen bundle.

| Input | Required by this replay |
| --- | --- |
| `trace.train`, `trace.day1`, `trace.day2` | Fixed 48-hour training slice and two nonoverlapping one-day validation windows, with `start`, `end` (seconds) and ordered `requests`. Rows are `[request_id, app_id, arrival_s, service_s]`; IDs are unique within a window. |
| `configs` | Mapping from app IDs to native `memory_gb`, `cpu`, `concurrency` and `min_scale`. |
| `physics` | Fixed `seed`, preparation samples `prep_s`, complete-run drain bound `tail_limit_s`, and the TRAIN-derived `train_budget_24h_gbs`. |
| `calibration` | Kind observations carrying `duration`, `capacity`, `trial_id`, handler time `h`, `signal_available`, and scheduler-cache release bounds `r_lo`, `r_hi`. The ordered six contended trials for each cleanup duration (2 s and 5 s) determine the release law. Times are in seconds relative to retirement intent. |

## Closed-loop simulation and grid audit

The runners all accept the assembled bundle and require new output directories.

```sh
# Optional single-cell smoke test (needs the prepared bundle).
python3 -B code/ibm/run.py --bundle /path/to/inputs.json.gz --out /scratch/ibm_smoke --workers 1 --smoke

# Run the full archived 58-cell grid.
python3 -B code/ibm/run.py --bundle /path/to/inputs.json.gz --out /scratch/ibm_replay --workers 4

# Independently check the complete replay; this audit does not accept smoke runs.
python3 -B code/ibm/audit.py /scratch/ibm_replay --bundle /path/to/inputs.json.gz --out-dir /scratch/ibm_audit

# Run the separate TRAIN-only re-budget and the archived finite TTL frontier.
python3 -B code/ibm/rebudget.py --bundle /path/to/inputs.json.gz --out /scratch/ibm_train --workers 4
python3 -B code/ibm/frontier.py --bundle /path/to/inputs.json.gz --archived-ttl60 /scratch/ibm_replay/summary.json \
  --train /scratch/ibm_train --out /scratch/ibm_frontier --workers 4
python3 -B code/ibm/audit_frontier.py /scratch/ibm_frontier --bundle /path/to/inputs.json.gz \
  --kernel code/ibm/kernel_v1.py --archive /scratch/ibm_replay --archive-audit /scratch/ibm_audit/audit.json \
  --train /scratch/ibm_train --out-dir /scratch/ibm_frontier_audit
python3 -B code/ibm/verify_grid.py --reference /path/to/evidence/ibm/grid-reference \
  --ttl60 /scratch/ibm_replay --rebudget /scratch/ibm_train --frontier /scratch/ibm_frontier
```

The grids cover the inherited TTL60 runs (58 cells), the re-budgeting selection
and evaluation (72 TRAIN and 58 validation), and the complete finite frontier
(144 validation cells). The verifiers compare each matching role and TTL while
excluding elapsed wall runtime. All validation summaries must agree exactly.
For TRAIN only, the cost and tail totals permit an absolute difference of
`2e-9 GB·s` to allow cross-platform floating-point summation; eligibility,
selection and other fields must still agree exactly. The already inspected
validation days are not independent platform repetitions.

## Later operating points, including TTL5

```sh
python3 -B code/ibm/verify_operating.py --evidence /path/to/evidence/ibm/operating \
  --bundle work/inputs.json.gz --out work/ibm-operating --rerun all --workers 4
```

The independent readback calculates wait, cost, completion and budget status
from 80 raw ledgers, verifies TRAIN-only selections and the separation of
the horizon from the one-second watchdog, and audits the four legacy TTL60
oracle ledgers. It checks the 12 selected TTL5 comparisons and counts the
oracle wait-until events and observed claim times. `--rerun all` re-simulates all
84 and compares complete ledgers (ignoring elapsed wall runtime only).
Use `--rerun selected` to rerun just the 12 selected and four legacy
cells, or omit it for the full readback without rerunning the simulator.

## Fixed-policy release-sensitivity results

For the older fixed-policy release-sensitivity results, the raw-ledger audit
requires NumPy for numeric NPZ arrays (loaded without pickle). It independently
recomputes all 210 cells, 174 request-paired contrasts, and 70 policy rankings
from saved request, instance and attribution ledgers, rather than relying on a
serialized final result. It does not execute the older historical Planner.

```sh
python3 -B code/ibm/audit_release_ledgers.py --evidence /path/to/evidence/ibm/release-sensitivity \
  --bundle work/inputs.json.gz --out work/ibm-release
```

For a lightweight archive-only check of hash integrity and the summarized
wait/cost/rank arithmetic, run
`python3 -B code/ibm/verify_release_sensitivity.py /path/to/evidence/ibm/release-sensitivity`.
