# Serverless Reservation Release under Contention

Reproduction artifact for *Serverless Reservation Release under Contention:
Measurement, Placement Prediction, and Reclamation Decisions*, by Jaemin
Yang, Bitchan Eom, and Eunchan Kim.

The artifact recomputes the paper's numbers and figures offline. It uses
archived measurements for the Kubernetes/Kind results and can rerun the
closed-loop IBM simulations. It neither starts a cluster nor trains PPO.

The [paper result tables](paper-results/README.md) are readable directly on
GitHub. The evidence needed to recompute them is a checksummed asset of the
[v1.1.0 GitHub release](https://github.com/Jay3404/serverless-reservation-release/releases/tag/v1.1.0).
The runner downloads it automatically: no GitHub login, private link, or lab
server is required. It includes the separate 12-trial calibration and new
48-trial within-setting Kind evaluation.

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
│   ├── kind/          Kind HTTP, control, first-decision replay and within-setting evaluation
│   ├── ibm/           IBM input preparation, simulation and ledger checks
│   ├── ppo/           Supplementary comparison from request/instance ledgers
│   └── figures/       Figure generators (Figure 6 is under code/kind/within_setting/)
└── data/
    ├── README.md      Where each input comes from
    ├── public-evidence.json  # Release tag, size and checksum for automatic download
    ├── download_ibm.py
    ├── prepare_within_setting.py
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

Use Python 3.11 or 3.12 for the pinned dependencies (tested with Python 3.12).
Clone the repository as above, use `git checkout v1.1.0` to pin this artifact
version, and choose a new output directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -B reproduce.py --out results --full-ibm --rerun-operating --paper-boundary-figure \
  --font-dir "$(python -c 'import matplotlib; print(matplotlib.get_data_path())')/fonts/ttf"
```

The command downloads the GitHub release archive, verifies its size and SHA-256
as well as its member checksums, rebuilds `inputs.json.gz`, and writes new
reports, figures and a per-step exit record. For a faster analysis that does
not regenerate the IBM event streams, omit `--full-ibm` and `--rerun-operating`.
Use `--no-figures` for numerical results only or `--evidence PATH` for an
already downloaded ZIP or an extracted directory. Korean versions of Figures
3, 5 and 6 additionally accept `--korean-font "$(kpsewhich UnDotum.ttf)"` if
the UnDotum font is installed. Leave sufficient scratch space for the new
ledgers. Rendering library versions and PDF timestamps may affect file bytes.

The evidence and output paths for each result are listed in
[`RESULTS.md`](RESULTS.md). Scripts do not connect to Kubernetes, train a model,
or treat a reconstructed decision as a measured HTTP latency.

| Task | Run guide | Required input |
| --- | --- | --- |
| Recalculate historical and 56-/96-trial Bind-prediction results | [Prediction](code/prediction/README.md) | Normalized calibration, forecast and endpoint events |
| Rescore the HTTP crossover, original Kind campaigns and new 12+48-trial within-setting evaluation | [Kind](code/kind/README.md) | Trial, assignment and calibration records in the GitHub release |
| Rebuild IBM inputs, rerun closed-loop simulations and audit release accounting | [IBM](code/ibm/README.md) | Normalized request windows, calibration, and archived event ledgers |
| Recalculate the PPO appendix | [PPO](code/ppo/README.md) | Recorded request and instance ledgers |
| Regenerate publication figures | [Figures](code/figures/README.md) | CSVs produced by the other components |

For citation, use the [artifact metadata](CITATION.cff) and cite the specific Git
commit used for your analysis.
