# Offline Kind scoring and decision replay

The Kind scoring programs run offline and require only Python's standard
library. The [root command](../../README.md#recompute-the-results) downloads
the public release and scores the original campaigns and the new
[within-setting physical evaluation](within_setting/README.md). It neither
creates clusters nor requires credentials.

## Reproduce from the extracted release evidence

For an individual reanalysis, use the verified `kind/` evidence component
extracted by the root command and run from the repository root:

```sh
python3 -B code/kind/reproduce.py --evidence /path/to/evidence/kind --out outputs/kind
```

`reproduce.py` derives the four-arm Kind comparison from 48 recorded trial
receipts and recalibrates the setting-specific decisions from the eight separate
calibration trials. It verifies matching Pod identities, API and scheduler Bind
receipts, reservation integration and calibration/measurement separation. It
also recalculates the earlier 48-trial forced-action comparison with an audit
of the 12 forced-wait trials, and refits/evaluates the HTTP crossover on its
32 calibration, 64 evaluation and 64 confirmation records. The HTTP reports
include the eight-block interaction between contention and shutdown choice.

Outputs include `baseline/block_pairs.csv` and `replay/first_decisions.csv`
for Figure 5, full trial/group CSVs under `baseline/` and `forced/`, and
three calibrated/recomputed HTTP JSON reports under `http/`. The decision
replay estimates no unobserved response time; the late/early HTTP comparison
uses only measured timestamps from the original trials.

`build_evidence.py` exports only the necessary fixture receipts from separately
authorized original archives (`--help` documents the inputs). It does not
include full Kubernetes objects, cluster credentials, inventories, host logs,
or launch code. Content hashes in the minimized Kind receipts are provenance
of the archived inputs, not a claim that these derived records preserve those
raw bytes. The three normalized HTTP input files were already sealed and are
preserved byte-for-byte for the frozen scorer. A portable score is distinct
from verification of an original private log or a new platform measurement.

## Optional: work with the original archived layout

From the repository root, run the data-free scoring contract test:

```sh
python3 -B code/kind/verify_kind.py test
```

The synthetic test checks planned-but-unrun counts and confirms that missing measurements are not turned into zeros.

To rescore the 48-trial, four-arm Kind evaluation, supply the archived receipts and separate calibration. Both outputs must point to new directories.

```sh
SOURCE=/path/to/archived/fgcs_baselines_20261118
python3 -B code/kind/verify_kind.py score \
  --evaluation "$SOURCE/evaluation" --calibration "$SOURCE/calibration" \
  --output checked-kind48
```

The input consists of stage plans, terminal receipts, the calibration seal, trial JSON and associated binding-audit and scheduler-event receipts. Scoring verifies the pinned code and protocol hashes, validates the trial pairing, and checks calibration–evaluation identity separation when the calibration directory is provided. It writes per-trial scores, block-paired contrasts, grouped summaries, and an input SHA-256 manifest. Negative latency contrasts mean the signal arm was earlier. See [the frozen scoring contract](frozen/fgcs_baselines_20261118/SCORING.md) for denominators, timestamp definitions, missing-data rules, and limits.

To replay the setting-specific fixed baseline on the original observed states, also supply the original provenance archive. `--source` must contain the original `calibration/`, `evaluation/`, and `scored/` directories. `--provenance-root` must contain the original `.research/fgcs_baselines_20261118/` files `KIND_PROTOCOL.md`, `run_kind_baselines.py`, `score_kind.py` and `SCORING.md`, and `.research/fgcs_retirement_action_20261117/` files `kind_base.py` and `run_kind.py`. The external source files are read for checksum verification only and are never executed.

```sh
python3 -B code/kind/verify_kind.py replay --source "$SOURCE" \
  --provenance-root /path/to/extracted/archive --output checked-setting-replay
```

Replay estimates the configuration-specific return constants from calibration before reading evaluation rows. It reconstructs decisions at recorded states; it cannot measure HTTP latency or reservation cost for an arm that was not physically executed. The six blocks are repeated trials on one host, not six independent clusters in production. Neither command reproduces new physical measurements or quantifies reclaimed physical memory.

The files under `frozen/` are byte-exact copies of archived analysis files. The
`verify_kind.py` entry point resolves original archive paths without modifying
the seal checks, metric definitions, or receipt format. The root runner instead
uses the publicly released projections of those records.
