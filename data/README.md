# Data and evidence

This directory contains acquisition instructions and a downloader, not datasets,
prepared replay bundles, or experimental logs.

## Offline evidence for the manuscript

`reproduce.py` reads the separately supplied `fgcs-offline-evidence.zip` or
its extracted directory. A complete archive has an `evidence-manifest.json`
and four subdirectories: `ibm/`, `kind/`, `prediction/` and `ppo/`. The
runner validates every entry before using it. It reads only data, not scripts
from the ZIP. The archive is not stored or mirrored in this Git repository.
The accompanying archive has SHA-256:

```text
719534a37ae86393de420b8487e52036f8581bd8ef02ff2a026e7c7688e583a7
```

Verify it with `shasum -a 256 fgcs-offline-evidence.zip` before using it.

| Input | Source and use |
| --- | --- |
| IBM Cloud Code Engine raw trace | [Public publisher release][ibm], pinned below. Contains the invocation, execution-time, and per-app configuration records underlying the IBM replay. |
| Normalized IBM replay inputs | Archived request windows, fixed application configurations, simulator parameters and measured-delay calibration. [The preparation script](../code/ibm/prepare.py) assembles them into the exact `inputs.json.gz` consumed by the simulator. |
| Kind and Bind-prediction records | Experimental assignments, relevant event timestamps, and archived calibration and evaluation records. None can be derived from the IBM publication. |
| IBM and PPO result ledgers | Archived per-request and instance records, operating-point receipts, and result tables for independent accounting and parity checks. The PPO comparison does not need archived weights or GPU training. |

Use only evidence copies you are authorized to access. If the separate inputs
are unavailable, the root guide lists synthetic checks that run without them.
`build_evidence_zip.py` only packages an already reviewed input directory;
it does not redact data or publish an archive.

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
