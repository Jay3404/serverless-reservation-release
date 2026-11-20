"""Compute the paper's archived analyses without a Kubernetes cluster."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parent
SCHEMA = "fgcs-offline-evidence-v1"
COMPONENTS = {"ibm", "kind", "ppo", "prediction"}
DATA_SUFFIXES = {".csv", ".gz", ".json", ".md", ".npz", ".npy"}
LIMIT_BYTES = 8 * 1024**3
MANIFEST = "evidence-manifest.json"


def need(ok, message):
    if not ok:
        raise ValueError(message)


def plain_path(name):
    return (isinstance(name, str) and name.count("/") >= 1
            and name.split("/")[0] in COMPONENTS
            and all(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", slug)
                    for slug in name.split("/"))
            and PurePosixPath(name).suffix in DATA_SUFFIXES)


def read_manifest(raw):
    need(len(raw) < 8 * 1024**2, "Oversized evidence manifest")
    doc = json.loads(raw)
    need(doc.get("schema") == SCHEMA and isinstance(doc.get("files"), dict), "Wrong evidence schema")
    files = doc["files"]
    need({name.split("/")[0] for name in files} == COMPONENTS and len(files) <= 20_000,
         "Evidence must cover all four components")
    for name, row in files.items():
        need(plain_path(name), f"Unsafe or unsupported evidence name: {name!r}")
        need(type(row.get("bytes")) is int and row["bytes"] >= 0
             and re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", "")), f"Malformed checksum for {name!r}")
    need(sum(row["bytes"] for row in files.values()) <= LIMIT_BYTES, "Evidence exceeds extraction bound")
    return files


def measure(source, expected, output=None):
    digest, seen = hashlib.sha256(), 0
    for block in iter(lambda: source.read(1 << 20), b""):
        seen += len(block)
        need(seen <= expected["bytes"], "Evidence member larger than manifest")
        digest.update(block)
        if output:
            output.write(block)
    need(seen == expected["bytes"] and digest.hexdigest() == expected["sha256"], "Evidence checksum mismatch")


def validated_evidence(source, dest):
    if source.is_dir():
        canonical = source.resolve()
        files = read_manifest((source / MANIFEST).read_bytes())
        for name, expected in sorted(files.items()):
            path = source / name
            need(path.resolve().is_relative_to(canonical) and path.is_file() and not path.is_symlink(),
                 f"Invalid evidence input {name}")
            try:
                with path.open("rb") as f:
                    measure(f, expected)
            except ValueError as e:
                raise ValueError(f"{name}: {e}") from e
        # Do not let a component ingest an unlisted file from an abandoned run.
        need({p.relative_to(source).as_posix() for p in source.rglob("*")
              if p.is_file() or p.is_symlink()} == set(files) | {MANIFEST},
             "Evidence directory contains untracked files")
        return source, len(files)

    with ZipFile(source) as archive:
        index = {item.filename: item for item in archive.infolist()}
        need(len(index) == len(archive.infolist()) and MANIFEST in index, "Duplicate or missing ZIP entries")
        need(index[MANIFEST].file_size < 8 * 1024**2, "Oversized evidence manifest")
        raw = archive.read(MANIFEST)
        files = read_manifest(raw)
        need(set(index) == set(files) | {MANIFEST}, "Evidence ZIP has an unexpected member")
        dest.mkdir()
        (dest / MANIFEST).write_bytes(raw)
        for name, expected in sorted(files.items()):
            item = index[name]
            kind = stat.S_IFMT(item.external_attr >> 16)
            need(not item.is_dir() and kind in (0, stat.S_IFREG) and item.file_size == expected["bytes"],
                 f"Invalid ZIP member: {name}")
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(item) as stream, target.open("xb") as f:
                    measure(stream, expected, f)
            except ValueError as e:
                raise ValueError(f"{name}: {e}") from e
    return dest, len(files)


def run(source, out, workers, figures=True, full_grid=False, rerun_operating=False,
        korean_font=None, font_dir=None, paper_boundary=False):
    out.mkdir(parents=True, exist_ok=False)
    evidence, count = validated_evidence(source, out / "_evidence")
    logs = out / "logs"
    logs.mkdir()
    stages = []

    def step(name, path, *args):
        print(f"[{name}]", flush=True)
        cmd = [sys.executable, "-B", str(ROOT / path), *(str(arg) for arg in args)]
        with (logs / f"{name}.log").open("x", encoding="utf-8") as stream:
            proc = subprocess.run(cmd, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        stages.append(dict(step=name, exit_code=proc.returncode))
        if proc.returncode:
            raise RuntimeError(f"{name} failed; see {logs / (name + '.log')}")

    IBM, K, P = (evidence / v for v in ("ibm", "kind", "prediction"))
    bundle = out / "inputs.json.gz"
    try:
        step("ibm_prepare", "code/ibm/prepare.py", "build", IBM / "normalized", bundle)
        step("kind", "code/kind/reproduce.py", "--evidence", K, "--out", out / "kind")
        step("prediction", "code/prediction/reproduce.py", "--evidence", P,
             "--out", out / "prediction", *(["--figures"] if figures else []),
             *(["--font-dir", font_dir] if font_dir and figures else []))
        step("ibm_sensitivity", "code/ibm/verify_release_sensitivity.py", IBM / "release-sensitivity")
        step("ibm_sensitivity_ledgers", "code/ibm/audit_release_ledgers.py", "--evidence",
             IBM / "release-sensitivity", "--bundle", bundle, "--out", out / "ibm-release")
        step("ibm_operating", "code/ibm/verify_operating.py", "--evidence", IBM / "operating",
             "--bundle", bundle, "--out", out / "ibm-operating", "--workers", workers,
             *(["--rerun", "all"] if rerun_operating else []))
        step("ibm_decisions", "code/ibm/decision_audit.py", "score", "--bundle", bundle,
             "--evidence", IBM / "decision-audit.json.gz", "--out", out / "decision-audit.json")
        step("ppo_appendix", "code/ppo/reproduce.py", "--evidence", evidence / "ppo", "--out", out / "ppo.json")
        if figures:
            ko = ["--language", "both", "--korean-font", korean_font] if korean_font else []
            step("figure_5", "code/figures/plot_figure5.py", "--first",
                 out / "kind" / "replay/first_decisions.csv", "--pairs", out / "kind" / "baseline/block_pairs.csv",
                 "--out", out / "figures", *ko)
            if paper_boundary:
                step("figure_3_paper_en", "code/figures/plot_boundary_paper.py", "--gains",
                     out / "prediction/boundary_gains.csv", "--output", out / "figures/boundary-gain-en.pdf")
                if korean_font:
                    step("figure_3_paper_ko", "code/figures/plot_boundary_paper.py", "--gains",
                         out / "prediction/boundary_gains.csv", "--language", "ko",
                         "--korean-font", korean_font, "--output", out / "figures/boundary-gain-ko.pdf")

        if full_grid:
            ttl60, train, frontier = (out / n for n in ("ttl60", "rebudget", "frontier"))
            step("ibm_ttl60", "code/ibm/run.py", "--bundle", bundle, "--out", ttl60, "--workers", workers)
            step("ibm_train", "code/ibm/rebudget.py", "--bundle", bundle, "--out", train, "--workers", workers)
            step("ibm_frontier", "code/ibm/frontier.py", "--bundle", bundle, "--archived-ttl60",
                 ttl60 / "summary.json", "--train", train, "--out", frontier, "--workers", workers)
            step("ibm_grid_parity", "code/ibm/verify_grid.py", "--reference", IBM / "grid-reference",
                 "--ttl60", ttl60, "--rebudget", train, "--frontier", frontier)
            step("ibm_ttl60_audit", "code/ibm/audit.py", ttl60, "--bundle", bundle,
                 "--out-dir", out / "ttl60-audit")
            step("ibm_frontier_audit", "code/ibm/audit_frontier.py", frontier, "--bundle", bundle,
                 "--archive", ttl60, "--archive-audit", out / "ttl60-audit/audit.json",
                 "--kernel", ROOT / "code/ibm/kernel_v1.py", "--train", train,
                 "--out-dir", out / "frontier-audit")
    finally:
        with (out / "run-report.json").open("x", encoding="utf-8") as f:
            json.dump(dict(validated_evidence_files=count, steps=stages,
                           full_grid_requested=full_grid, operating_rerun_requested=rerun_operating,
                           figures_requested=figures), f, indent=2)
            f.write("\n")
    print(f"Finished requested stages. See {out / 'run-report.json'}")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--evidence", required=True, type=Path, help="separate authorized evidence ZIP or extracted directory")
    cli.add_argument("--out", type=Path, required=True, help="new results directory")
    cli.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    cli.add_argument("--no-figures", action="store_true")
    cli.add_argument("--korean-font", type=Path, help="optional UnDotum.ttf for Korean plots")
    cli.add_argument("--font-dir", type=Path, help="optional DejaVu Sans directory for Figures 1–4")
    cli.add_argument("--paper-boundary-figure", action="store_true",
                     help="also render Figure 3 with the manuscript layout (requires Matplotlib)")
    cli.add_argument("--full-ibm", action="store_true", help="replay and audit the historical TTL60/TRAIN/DEV grids")
    cli.add_argument("--rerun-operating", action="store_true", help="also replay the operating and oracle cells")
    a = cli.parse_args()
    if sys.flags.optimize:
        cli.error("Do not use Python optimization: the archived checks require assertions.")
    run(a.evidence.resolve(), a.out.resolve(), a.workers, figures=not a.no_figures, full_grid=a.full_ibm,
        rerun_operating=a.rerun_operating, korean_font=a.korean_font.resolve() if a.korean_font else None,
        font_dir=a.font_dir.resolve() if a.font_dir else None, paper_boundary=a.paper_boundary_figure)


if __name__ == "__main__":
    main()
