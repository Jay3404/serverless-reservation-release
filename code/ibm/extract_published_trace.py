#!/usr/bin/env python3
"""Build the fixed paper windows from authenticated IBM trace archives.

The offline replay does not need this optional step: its evidence ZIP already
supplies the same normalized windows. The bootstrap mapping and empirical Kind
timing metadata are inputs, not measurements that can be inferred from IBM.
Every output must match the canonical prepared-input digest before it is saved.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import runpy
import shutil
import subprocess
from tempfile import TemporaryFile

import prepare as bundle


BOUNDS = {
    "train": (17994.129, 190794.12900000002),
    "day1": (4009677.3329000007, 4096077.3329000007),
    "day2": (4096077.3329000007, 4182477.3329000007),
}
# From the original all-row audit of pinned publisher weeks. Only week 1
# intersects TRAIN; days 1/2 overlap week 7 only. A missing request, changed
# boundary, or different source order also fails the exact bundle digest.
WEEK_WINDOWS = {1: ("train",), 7: ("day1", "day2")}
SCHEMA = "FGCS_IBM_FIXED_COHORT_MAPPING_V1"
FIELD = {"memory_gb": "AppContainerRequestMemory", "cpu": "AppContainerRequestCpu",
         "concurrency": "AppContainerConcurrency", "min_scale": "AppMinScale"}


def read(path):
    with path.open("rb") as stream:
        return bundle.parse(stream)


def key(row):
    return str(row["NamespaceHash"]), str(row["AppHash"])


def mapping(path):
    data = read(path)
    bundle.require(data["schema"] == SCHEMA and len(data["cohort"]) == 32, "Unknown fixed cohort schema")
    apps = sorted(data["cohort"], key=lambda entry: int(entry["app_id"]))
    bundle.require([x["app_id"] for x in apps] == [str(i) for i in range(32)]
                   and len({key(x) for x in apps}) == 32
                   and len({x["parent_index"] for x in apps}) == 32,
                   "Incorrect fixed cohort keys or index mapping")
    return apps


def derive_mapping(source, pilot, out):
    original = read(source)
    pilot_rows = read(pilot)
    source_by_id = {x["index"]: x for x in original["apps"]}
    bundle.require(len(source_by_id) == len(original["apps"]) == 599 and len(pilot_rows) == 32,
                   "Unexpected archived original and pilot cohorts")
    result = {"schema": SCHEMA, "cohort": [{"app_id": str(x["app_id"]), "parent_index": x["original_app_id"],
                "NamespaceHash": source_by_id[x["original_app_id"]]["NamespaceHash"],
                "AppHash": source_by_id[x["original_app_id"]]["AppHash"]} for x in pilot_rows]}
    with out.open("xb") as stream:
        stream.write(bundle.encode(result))


def events(stock, times, duration, app, window):
    """Copy the half-open window in input order without rebasing or deduping."""
    begin, end = BOUNDS[window]
    selected = (times >= begin) & (times < end)
    stock[window].extend((float(t), app, float(d)) for t, d in zip(times[selected], duration[selected]))


def pack(data, stock, out):
    """Write only after canonical end-to-end equivalence is established."""
    trace = {}
    for w, values in stock.items():
        # Python sort, like the original stable chronological NumPy sort,
        # preserves publisher week/frame/within-row order for ties.
        values.sort(key=lambda r: r[0])
        rows = [[str(i), app, t, d] for i, (t, app, d) in enumerate(values)]
        start, end = BOUNDS[w]
        trace[w] = {"start": start, "end": end, "requests": rows, "duration_s": bundle.WINDOWS[w][1],
                    "request_count": len(rows), "active_apps": len({row[1] for row in rows})}
    info = {"schema": data["schema"], "trace": trace, **data}
    bundle.validate(info)
    digest = bundle.content_id(info)
    out.mkdir(parents=True, exist_ok=False)
    with (out / "metadata.json").open("xb") as stream:
        stream.write(bundle.encode(data))
    for w, v in trace.items():
        bundle.write_gzip(out / f"{w}.json.gz", bundle.encode(v))
    print(f"Verified canonical replay content SHA-256: {digest}")


def archived_arrays(root, apps, stock):
    """Read the original publisher-derived 599-app arrays (never pickle)."""
    import numpy as np

    for split, windows in {"train": ("train",), "dev": ("day1", "day2")}.items():
        t, a, s = (np.load(root / f"{split}_{name}.npy", mmap_mode="r", allow_pickle=False)
                   for name in ("arrivals", "apps", "durations"))
        bundle.require(t.shape == a.shape == s.shape and t.ndim == 1 and np.all(np.diff(t) >= 0.),
                       "Parent data arrays are not aligned and sorted")
        for w in windows:
            lo, hi = (int(idx) for idx in np.searchsorted(t, BOUNDS[w], side="left"))
            alias = {x["parent_index"]: x["app_id"] for x in apps}
            stock[w].extend((float(t[i]), alias[int(a[i])], float(s[i]))
                            for i in range(lo, hi) if int(a[i]) in alias)


def verify_volume_set(root):
    """Use the public downloader's pinned manifest and Git-blob hash verifier."""
    dl = runpy.run_path(str(Path(__file__).resolve().parents[2] / "data/download_ibm.py"))
    manifest = dl["get_files"]()
    groups = ["app_configs.7z", *(f"week_{week}.7z." for week in WEEK_WINDOWS)]
    for group in groups:
        expected = {p for p in manifest if p.startswith(f"compressed_data/{group}")}
        actual = {f"compressed_data/{p.name}" for p in (root / "compressed_data").glob(group + "*")}
        bundle.require(expected and expected == actual, f"Missing or additional compressed volumes: {group}")
        for name in sorted(expected):
            file = dl["local_path"](root, name)
            bundle.require(file.is_file(), "Not a regular publisher archive volume")
            with file.open("rb") as content:
                dl["check_stream"](content, *manifest[name])


@contextmanager
def from_7zip(seven_zip, file, member):
    # No archive member is written to disk. The documented/verified publisher
    # pickle is executable input, hence the explicit opt-in in main().
    import pickle

    with TemporaryFile() as error_stream:
        listing = subprocess.run([seven_zip, "l", "-slt", str(file)], capture_output=True,
                                 text=True, check=True).stdout
        paths = [line[7:] for line in listing.splitlines() if line.startswith("Path = ")
                 and re.search(r"\.(pickle|pkl)$", line, re.IGNORECASE)]
        bundle.require(paths == [member], f"Unexpected publisher member list in {file.name}")
        with subprocess.Popen([seven_zip, "x", "-so", str(file), member], stdout=subprocess.PIPE,
                              stderr=error_stream, bufsize=1 << 20) as process:
            assert process.stdout is not None
            try:
                frame = pickle.load(process.stdout)
                bundle.require(not process.stdout.read(1) and process.wait() == 0,
                               f"Invalid archive payload or CRC for {file.name}")
            except BaseException:
                if process.poll() is None:
                    process.kill()
                raise
        yield frame


def check_config(frame, by_key, frozen, np, pd):
    bundle.require(isinstance(frame, pd.DataFrame), "Publisher configs are not a dataframe")
    seen = set()
    for _, row in frame.iterrows():
        alias = by_key.get(key(row))
        if alias is None:
            continue
        bundle.require(alias not in seen, "Duplicate publisher configuration")
        seen.add(alias)
        for label, column in FIELD.items():
            v = np.asarray(row[column], dtype=np.float64)
            bundle.require(v.shape == (62,) and np.all(np.isfinite(v)) and np.all(v == frozen[alias][label]),
                           f"Nonstatic or unexpected publisher configuration ({label}, app {alias})")
    bundle.require(seen == set(frozen), "Publisher configuration lacks fixed cohort apps")


def scan_week(frame, w, by_key, stock, np, pd):
    bundle.require(isinstance(frame, pd.DataFrame), "Publisher traffic is not a dataframe")
    for _, row in frame.iterrows():
        alias = by_key.get(key(row))
        if alias is None:
            continue
        t, ms = (np.asarray(row[c], dtype=np.float64) for c in ("InvocationTimes", "AppExecTimes"))
        n = row["NumEvents"]
        bundle.require(t.ndim == ms.ndim == 1 and t.shape == ms.shape and t.size == n
                       and np.all(np.isfinite(t)) and np.all(np.isfinite(ms)) and np.all(ms >= 0.),
                       "Malformed or nonfinite publisher trace row in fixed cohort")
        for window in WEEK_WINDOWS[w]:
            events(stock, t, ms / 1000.0, alias, window)


def publisher(root, seven_zip, apps, data, stock):
    # Lazy imports keep the prebuilt-evidence route free of these dependencies.
    import numpy as np
    import pandas as pd

    verify_volume_set(root)
    by_key = {key(x): x["app_id"] for x in apps}
    c = root / "compressed_data"
    with from_7zip(seven_zip, c / "app_configs.7z", "app_configs.pickle") as frame:
        check_config(frame, by_key, data["configs"], np, pd)
    del frame
    for w in WEEK_WINDOWS:
        with from_7zip(seven_zip, c / f"week_{w}.7z.001", f"week_{w}.pickle") as frame:
            scan_week(frame, w, by_key, stock, np, pd)
        del frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    raw = sub.add_parser("publisher", help="authenticate and stream only the two relevant publisher weeks")
    raw.add_argument("--raw", type=Path, required=True, help="download_ibm.py output root")
    raw.add_argument("--sevenzip", default=shutil.which("7zz") or shutil.which("7z"))
    raw.add_argument("--trust-publisher-pickle", action="store_true",
                     help="explicit opt-in to loading authenticated publisher pickle code")
    arr = sub.add_parser("arrays", help="rebuild from the original authenticated prepared parent arrays")
    arr.add_argument("--arrays", type=Path, required=True, help="original 599-app NPY inputs")
    for p in (raw, arr):
        p.add_argument("--mapping", type=Path, required=True, help="frozen raw app mapping from study evidence")
        p.add_argument("--metadata", type=Path, required=True, help="normalized study metadata.json including Kind calibration")
        p.add_argument("--out", type=Path, required=True, help="new output directory")
    make = sub.add_parser("derive-mapping", help="export the fixed mapping from two original local preparation manifests")
    make.add_argument("--source-metadata", type=Path, required=True)
    make.add_argument("--pilot-configs", type=Path, required=True)
    make.add_argument("--out", type=Path, required=True)
    a = parser.parse_args()
    if a.operation == "derive-mapping":
        derive_mapping(a.source_metadata, a.pilot_configs, a.out)
        return
    if a.operation == "publisher" and not (a.trust_publisher_pickle and a.sevenzip):
        parser.error("Publisher route requires 7zz/7z and --trust-publisher-pickle")
    apps, data = mapping(a.mapping), read(a.metadata)
    stock = {w: [] for w in BOUNDS}
    if a.operation == "publisher":
        publisher(a.raw.resolve(), a.sevenzip, apps, data, stock)
    else:
        archived_arrays(a.arrays, apps, stock)
    pack(data, stock, a.out)


if __name__ == "__main__":
    main()
