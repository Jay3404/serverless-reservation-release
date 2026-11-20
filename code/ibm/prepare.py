"""Assemble the published replay shape from the archived normalized inputs.

This code does not decompress the publisher's raw trace or invent replacement
timing measurements. It combines the fixed IBM windows, app configurations,
and independently measured calibration supplied with the evidence release.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path


SCHEMA = "IBM_LIFECYCLE_PORTABLE_INPUT_V1"
EXPECTED_JSON = "7133803d8f2d94991c8f5d709b3189aff5ae467a8b3ceff44d77f878411e015c"
EXPECTED_GZIP = "fac8f7f44ce7b34062631a86c36e5376da947f439ccb93ebfb42f415780badc0"
WINDOWS = {
    "train": (90087, 172800, 13),
    "day1": (25319, 86400, 22),
    "day2": (24800, 86400, 22),
}
HEADERS = {"schema", "configs", "physics", "calibration", "provenance"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def parse(stream):
    def object_of(pairs):
        mapping = dict(pairs)
        require(len(mapping) == len(pairs), "Duplicate JSON member")
        return mapping

    return json.loads(stream.read(), object_pairs_hook=object_of)


def encode(bundle):
    return (json.dumps(bundle, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def content_id(bundle):
    ident = hashlib.sha256(encode(bundle)).hexdigest()
    require(ident == EXPECTED_JSON, "Normalized input bytes differ from the frozen bundle")
    return ident


def validate(bundle):
    require(bundle["schema"] == SCHEMA and len(bundle["configs"]) == 32,
            "Unexpected trace schema or configured app count")
    for day, (n, seconds, active) in WINDOWS.items():
        window = bundle["trace"][day]
        rows = window["requests"]
        require(window["duration_s"] == seconds == window["end"] - window["start"]
                and window["request_count"] == len(rows) == n,
                f"Unexpected window duration or row count for {day}")
        require(window["active_apps"] == len({row[1] for row in rows}) == active,
                f"Unexpected active app count for {day}")
        require(all(len(row) == 4 and row[0] == str(i) and row[1] in bundle["configs"]
                    and window["start"] <= row[2] < window["end"] and row[3] >= 0
                    and (i == 0 or rows[i-1][2] <= row[2]) for i, row in enumerate(rows)),
                f"Malformed request row for {day}")
    trace = bundle["trace"]
    require(trace["train"]["end"] < trace["day1"]["start"]
            and trace["day1"]["end"] == trace["day2"]["start"],
            "Historical window boundaries changed")
    calib = bundle["calibration"]
    require(len(calib) == len({r["trial_id"] for r in calib}) == 24
            and all(r["condition"] == "calibration" and r["status"] == "complete" for r in calib),
            "Calibration rows must be 24 distinct completed calibration trials")
    for d in (2, 5):
        for cap in ("contended", "spare"):
            require(sum((r["duration"], r["capacity"]) == (d, cap) for r in calib) == 6,
                    "Calibration design changed")


def write_gzip(path, data):
    with path.open("xb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
        stream.write(data)


def build(directory, path):
    with (directory / "metadata.json").open("rb") as stream:
        metadata = parse(stream)
    require(set(metadata) == HEADERS, "Missing or unexpected metadata fields")
    trace = {}
    for day in WINDOWS:
        with gzip.open(directory / f"{day}.json.gz", "rb") as stream:
            trace[day] = parse(stream)
    bundle = {"schema": metadata["schema"], "trace": trace}
    bundle.update(metadata)
    validate(bundle)
    digest = content_id(bundle)
    write_gzip(path, encode(bundle))
    gz_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"content_sha256": digest, "gzip_sha256": gz_digest,
            "archived_compression_bytes_match": gz_digest == EXPECTED_GZIP,
            "windows": {k: v["request_count"] for k, v in trace.items()}}


def split(path, directory):
    """Create reviewable normalized pieces from a trusted frozen input file."""
    with gzip.open(path, "rb") as stream:
        bundle = parse(stream)
    validate(bundle)
    digest = content_id(bundle)
    directory.mkdir(parents=True, exist_ok=False)
    metadata = {k: v for k, v in bundle.items() if k != "trace"}
    with (directory / "metadata.json").open("xb") as output:
        output.write(encode(metadata))
    for day in WINDOWS:
        write_gzip(directory / f"{day}.json.gz", encode(bundle["trace"][day]))
    return {"content_sha256": digest, "windows": {k: v["request_count"] for k, v in bundle["trace"].items()}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    assemble = commands.add_parser("build", help="assemble the frozen replay bundle")
    assemble.add_argument("directory", type=Path, help="directory of normalized inputs")
    assemble.add_argument("path", type=Path, help="new .json.gz path")
    extract = commands.add_parser("split", help="extract normalized inputs from an existing frozen bundle")
    extract.add_argument("path", type=Path, help="existing .json.gz path")
    extract.add_argument("directory", type=Path, help="new output directory")
    args = p.parse_args()
    result = (build(args.directory, args.path) if args.command == "build"
              else split(args.path, args.directory))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
