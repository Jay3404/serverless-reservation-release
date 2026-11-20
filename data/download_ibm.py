#!/usr/bin/env python3
"""Download and check the pinned, public IBM Cloud Code Engine release."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen


REPOSITORY = "ubc-cirrus-lab/ibm-cloud-code-engine-traces"
REVISION = "06884363719b5ab6a3e1d8de46e919f0e6d1fd10"
TREE_URL = f"https://api.github.com/repos/{REPOSITORY}/git/trees/{REVISION}?recursive=1"
RAW_URL = f"https://raw.githubusercontent.com/{REPOSITORY}/{REVISION}"
EXPECTED_FILES = 77
EXPECTED_BYTES = 6_503_665_372
METADATA_FILES = {"README.md", "LICENSE", "CITATION.cff"}
PAPER_WINDOWS = ("compressed_data/app_configs.7z", "compressed_data/week_1.7z.",
                 "compressed_data/week_7.7z.")
CHUNK_SIZE = 1024 * 1024


def request(url, accept):
    return urlopen(Request(url, headers={
        "Accept": accept, "User-Agent": "fgcs-reservation-release-reproduction"
    }), timeout=120)


def get_files():
    with request(TREE_URL, "application/vnd.github+json") as response:
        tree = json.load(response)
    if tree.get("sha") != REVISION or tree.get("truncated") is not False:
        raise ValueError("Publisher listing is incomplete or does not match the pinned revision")
    files = {}
    for entry in tree["tree"]:
        if entry["type"] == "tree":
            continue
        path, size, sha = entry["path"], entry["size"], entry["sha"]
        parts = path.split("/")
        if (entry["type"] != "blob" or entry["mode"] not in {"100644", "100755"}
                or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) and part not in {".", ".."}
                           for part in parts)
                or path in files or not isinstance(size, int) or size < 0
                or not re.fullmatch(r"[0-9a-f]{40}", sha)):
            raise ValueError(f"Unexpected publisher tree entry: {path!r}")
        files[path] = (size, sha)
    if (len(files) != EXPECTED_FILES or sum(size for size, _ in files.values()) != EXPECTED_BYTES
            or not METADATA_FILES.issubset(files)):
        raise ValueError("Publisher tree file inventory does not match the pinned release")
    return files


def check_stream(source, size, sha, output=None):
    # Git hashes the blob header and the raw bytes, not just the raw payload.
    digest = hashlib.sha1(f"blob {size}\0".encode("ascii"))
    actual_size = 0
    for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
        actual_size += len(chunk)
        if actual_size > size:
            raise ValueError("Payload is larger than the pinned Git blob")
        digest.update(chunk)
        if output is not None:
            output.write(chunk)
    if actual_size != size or digest.hexdigest() != sha:
        raise ValueError("Byte count or Git blob hash does not match the pinned release")


def local_path(root, rel):
    parts = PurePosixPath(rel).parts
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"Refusing to use a symbolic link at {path}")
    return path


def fetch(root, files):
    root.mkdir(parents=True, exist_ok=True)
    pending = []
    for rel, (size, sha) in sorted(files.items()):
        dest = local_path(root, rel)
        if dest.exists():
            try:
                if not dest.is_file():
                    raise ValueError("not a regular file")
                with dest.open("rb") as source:
                    check_stream(source, size, sha)
            except ValueError as exc:
                raise ValueError(f"Existing file does not match; move it aside before retrying: {dest}") from exc
            print(f"Verified existing: {rel}", flush=True)
        else:
            pending.append((rel, dest, size, sha))
    missing_bytes = sum(size for _, _, size, _ in pending)
    if shutil.disk_usage(root).free < missing_bytes:
        raise ValueError(f"Not enough free disk space under {root}; need {missing_bytes:,} more bytes")
    for rel, dest, size, sha in pending:
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading: {rel} ({size:,} bytes)", flush=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=dest.parent, suffix=".part", delete=False) as output:
                temp = Path(output.name)
                with request(f"{RAW_URL}/{quote(rel)}", "application/octet-stream") as response:
                    check_stream(response, size, sha, output)
            # Insert only verified bytes and never overwrite an existing file.
            os.link(temp, dest)
        except (OSError, ValueError) as exc:
            raise ValueError(f"{rel}: {exc}") from exc
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="show pinned files and sizes without downloads or local writes")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--metadata-only", action="store_true", help="select only the publisher README, license, and citation")
    selection.add_argument("--paper-windows", action="store_true", help="select metadata, configurations, and weeks 1 and 7 only")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "raw" / "ibm-code-engine",
                        help="download directory (default: data/raw/ibm-code-engine)")
    args = parser.parse_args()
    try:
        files = get_files()
        if args.metadata_only:
            files = {name: files[name] for name in METADATA_FILES}
        elif args.paper_windows:
            files = {name: payload for name, payload in files.items()
                     if name in METADATA_FILES or name.startswith(PAPER_WINDOWS)}
        print(f"IBM Cloud Code Engine traces, revision {REVISION}")
        print(f"{len(files)} files, {sum(size for size, _ in files.values()):,} bytes")
        if args.list:
            for name, (size, sha) in sorted(files.items()):
                print(f"{size:>10}\t{sha}\t{name}")
        else:
            fetch(args.output, files)
            print(f"Verified files in {args.output}")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(1, f"download_ibm: {exc}\n")


if __name__ == "__main__":
    main()
