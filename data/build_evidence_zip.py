"""Create a checksummed ZIP from an already curated, authorized evidence directory.

This is not a redaction tool. It does not publish or fetch anything.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reproduce import COMPONENTS, MANIFEST, SCHEMA, need, plain_path, read_manifest


def pack(source, output):
    need(not output.is_relative_to(source), "Write the archive outside the input directory")
    need(set(p.name for p in source.iterdir()) == COMPONENTS, "Use a staging directory with only the four evidence components")
    entries = []
    for path in sorted(source.rglob("*")):
        name = path.relative_to(source).as_posix()
        need(not path.is_symlink(), f"Do not package symbolic links: {name}")
        if path.is_dir():
            continue
        need(path.is_file() and plain_path(name), f"Do not package unsupported data or source files: {name}")
        entries.append((name, path))
    manifest = {}
    with ZipFile(output, "x", compression=ZIP_DEFLATED, compresslevel=2, allowZip64=True) as zip_:
        for name, path in entries:
            h, size = hashlib.sha256(), 0
            with zip_.open(name, "w", force_zip64=True) as dst, path.open("rb") as src:
                for block in iter(lambda: src.read(1 << 20), b""):
                    h.update(block)
                    size += len(block)
                    dst.write(block)
            manifest[name] = dict(sha256=h.hexdigest(), bytes=size)
        content = json.dumps(dict(schema=SCHEMA, files=manifest), indent=2).encode() + b"\n"
        read_manifest(content)
        zip_.writestr(MANIFEST, content)
    print(f"{len(manifest)} validated member names; wrote {output}")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--source", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    pack(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
