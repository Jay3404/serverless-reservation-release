# Data and evidence

Evidence is distributed as a versioned asset of this repository's
[GitHub release](https://github.com/Jay3404/serverless-reservation-release/releases/tag/v1.1.0),
not stored as large files in Git history.

## Offline evidence for the manuscript

`python -B reproduce.py --out results` downloads the pinned release asset
without credentials. It also accepts a local `--evidence` ZIP or its extracted
directory. The full archive has an `evidence-manifest.json` and five
subdirectories: `ibm/`, `kind/`, `prediction/`, `ppo/` and `within-setting/`.
The runner validates the archive pin in [`public-evidence.json`](public-evidence.json)
and verifies every member before using it. It reads only data, not scripts
from the ZIP. A historical format with the first four components is still
accepted for old-run comparisons but does not reproduce the new evaluation.

| Input | Source and use |
| --- | --- |
| IBM Cloud Code Engine raw trace | [Public publisher release][ibm], pinned below. Contains the invocation, execution-time, and per-app configuration records underlying the IBM replay. |
| Normalized IBM replay inputs | Archived request windows, fixed application configurations, simulator parameters and measured-delay calibration. [The preparation script](../code/ibm/prepare.py) assembles them into the exact `inputs.json.gz` consumed by the simulator. |
| Kind and Bind-prediction records | Experimental assignments, relevant event timestamps, and archived calibration and evaluation records. None can be derived from the IBM publication. |
| IBM and PPO result ledgers | Archived per-request and instance records, operating-point receipts, and result tables for independent accounting and parity checks. The PPO comparison does not need archived weights or GPU training. |
| Within-setting Kind records | Sealed 12-trial calibration, separate 48-trial evaluation, and scoped scheduler, Bind, HTTP and signal evidence. `NORMALIZATION.json` in the component describes removal of unused host and Kubernetes identity metadata. Scored results are unchanged. |

The IBM inputs are publisher-derived research windows, not a redistribution
of the whole raw trace. The publisher copyright and [MIT notice](IBM-TRACE-LICENSE.txt)
are retained with the repository and in the archive. See the pinned source
below for its attribution and citation. The root guide also lists data-free
checks. `prepare_within_setting.py` projects lab records to the released audit
fields; `build_evidence_zip.py` packages already curated evidence and does not
perform privacy redaction or publish anything.

## Download the public IBM release

Python 3.10+ and internet access to `api.github.com` and
`raw.githubusercontent.com` suffice. Run from the repository root:

```sh
# Show the pinned file inventory and hashes; no dataset files are written.
python3 data/download_ibm.py --list

# Optional connectivity check: fetch only the README, license, and citation.
python3 data/download_ibm.py --metadata-only

# Fetch only app configurations and weeks 1 / 7 for the optional extraction.
python3 data/download_ibm.py --paper-windows

# Fetch the full pinned release to data/raw/ibm-code-engine/ (ignored by Git).
python3 data/download_ibm.py
```

Use `--output /path/to/ibm-code-engine` to choose an existing or new local
directory. The full release at commit
`06884363719b5ab6a3e1d8de46e919f0e6d1fd10` is **77 files**, totalling
**6,503,665,372 bytes** (about 6.5 GB). The downloader checks the exact byte
count and Git blob SHA-1 of each file against the pinned GitHub tree. It reuses
already verified files on rerun and refuses to overwrite mismatches. Run it in a
directory on a local filesystem that supports hard links. No account or token is
required; public GitHub rate limits can apply.

The downloader does not unpack the archives or open their pickle contents.
See the [IBM extraction guide](../code/ibm/TRACE_PREPARATION.md) for rebuilding
the fixed request windows from authenticated publisher volumes. That optional
path requires the frozen app mapping and empirical timing metadata from the
evidence archive. Exact bundle parity was checked starting from the archived
publisher-derived arrays, not by downloading the compressed volumes anew.
See the publisher's README and citation metadata for attribution and terms.

[ibm]: https://github.com/ubc-cirrus-lab/ibm-cloud-code-engine-traces/tree/06884363719b5ab6a3e1d8de46e919f0e6d1fd10
