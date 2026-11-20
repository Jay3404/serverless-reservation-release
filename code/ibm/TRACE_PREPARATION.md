# Optional preparation from the publisher's IBM archives

The fast offline replay uses the `ibm/normalized/` files from the separate
evidence bundle. This route does **not** need a multi-gigabyte download or
pickle loading. Use the optional publisher path below when auditing how the
fixed experimental request windows were extracted.

The raw trace does not supply the original measured Kind calibration, the frozen
cohort definition, or the archived budget and simulator parameters. The evidence
bundle therefore still supplies `ibm/trace-mapping.json` (32 mapped publisher
app keys) and `ibm/normalized/metadata.json` (the original non-request inputs).
This reconstructs the fixed cohort used in the paper; it does not select new apps
from run outcomes. All time ranges are half-open global publisher seconds. Each
request keeps its original time and app, with service time converted from
milliseconds to seconds. Tie order stays in publisher stream order.

From the repository root, download the relevant portion of the pinned IBM
release (about 1.27 GB):

```sh
python3 data/download_ibm.py --paper-windows --output /path/to/publisher
python3 -m pip install numpy==2.3.5 pandas==2.2.3
# Install a trusted 7zz or 7z executable in your environment as well.
python3 -B code/ibm/extract_published_trace.py publisher \
  --raw /path/to/publisher \
  --mapping /path/to/evidence/ibm/trace-mapping.json \
  --metadata /path/to/evidence/ibm/normalized/metadata.json \
  --trust-publisher-pickle --out /path/to/new-normalized-ibm
python3 -B code/ibm/prepare.py build /path/to/new-normalized-ibm /path/to/inputs.json.gz
```

The extractor checks the publisher-pinned Git-blob size and hash of the
configuration and the two required week archives **before** opening them. It
loads one weekly frame at a time from the decompressor's output, but still
requires enough RAM for the complete weekly frame. The publisher's pickles
are executable input; use the explicit opt-in only with those authenticated
archives and run in an environment suitable for trusted external data. No
raw archive is bundled with this repository. When the same archived
publisher-derived 599-app NPY arrays are separately available, `arrays`
replaces `publisher` with `--arrays <directory containing train/dev NPYs>`
and never enables pickle loading.

**Verification scope.** The `arrays` route was exercised on the original
publisher-derived arrays: all three windows and the rebuilt replay bundle
matched the archived artifacts byte-for-byte. The multi-gigabyte publisher
decompression route was not independently rerun for this release. The
extractor writes its outputs only after their canonical assembled JSON matches
the original SHA-256, so an unexpected dataset member, cohort, order, or timing
cannot silently produce a different experimental result.
