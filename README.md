# Serverless Reservation Release under Contention

Reproduction artifact for *Serverless Reservation Release under Contention:
Measurement, Placement Prediction, and Reclamation Decisions*, by Jaemin
Yang, Bitchan Eom, and Eunchan Kim.

The artifact recomputes the paper's numbers and figures offline. It uses
archived measurements for the Kubernetes/Kind results and can rerun the
closed-loop IBM simulations. It neither starts a cluster nor trains PPO.

The [paper result tables](paper-results/README.md) are readable directly on
GitHub. The repository does not include request-level ledgers. To recompute
the results, obtain the separate evidence archive described in
[`data/README.md`](data/README.md). The public IBM trace does not contain the
Kind measurements.

## Repository layout

```text
.
├── README.md
├── CITATION.cff
├── reproduce.py       # Offline pipeline and integrity checks
├── requirements.txt
├── paper-results/     # Paper tables, with units and source table references
├── code/
│   ├── prediction/    Bind-time predictions and error comparisons
│   ├── kind/          Kind HTTP, control and first-decision replay
│   ├── ibm/           IBM input preparation, simulation and ledger checks
│   ├── ppo/           Supplementary comparison from request/instance ledgers
│   └── figures/       Figures 1–5
└── data/
    ├── README.md      Where each input comes from
    ├── download_ibm.py
    └── build_evidence_zip.py
```

## Quick start: no data or cluster needed

Use Python 3.10+ on Linux or macOS. Clone this repository, then run the
checks from its root:

```sh
git clone https://github.com/Jay3404/serverless-reservation-release.git fgcs-reproduction
cd fgcs-reproduction

python3 -B test_reproduce.py
python3 -B -m unittest discover -s code/prediction -p 'test_*.py' -v
python3 -B code/kind/verify_kind.py test
python3 -B code/ibm/test_run.py
```

These checks use synthetic fixtures and do not need the study archive.

## Recompute the results

Use Python 3.11 or newer for the pinned dependencies (tested with Python 3.12).
The command accepts the separate ZIP or its extracted directory. Choose a
new output directory.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -B reproduce.py --evidence /path/to/fgcs-offline-evidence.zip --out results \
  --full-ibm --rerun-operating
```

The command verifies the evidence checksums, rebuilds `inputs.json.gz`,
recalculates the results, and writes new reports, figures, and a per-step exit
record. For a faster analysis that does not regenerate the IBM event streams,
omit `--full-ibm` and `--rerun-operating`. Use `--no-figures` for a
numerical-only run. Leave sufficient scratch space for the new ledgers.

The evidence and output paths for each result are listed in
[`RESULTS.md`](RESULTS.md). Scripts do not connect to Kubernetes, train a model,
or treat a reconstructed decision as a measured HTTP latency.

| Task | Run guide | Required input |
| --- | --- | --- |
| Recalculate historical and 56-/96-trial Bind-prediction results | [Prediction](code/prediction/README.md) | Normalized calibration, forecast and endpoint events |
| Rescore the HTTP crossover, both Kind 48-trial campaigns and the setting-aware comparison | [Kind](code/kind/README.md) | Archived trial, assignment and calibration records |
| Rebuild IBM inputs, rerun closed-loop simulations and audit release accounting | [IBM](code/ibm/README.md) | Normalized request windows, calibration, and archived event ledgers |
| Recalculate the PPO appendix | [PPO](code/ppo/README.md) | Recorded request and instance ledgers |
| Regenerate publication figures | [Figures](code/figures/README.md) | CSVs produced by the other components |

For citation, use the [artifact metadata](CITATION.cff) and cite the specific Git
commit used for your analysis.
