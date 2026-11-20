#!/usr/bin/env python3
"""Run the frozen, offline-only Kind scoring and action-replay programs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import runpy
import subprocess
import sys
from tempfile import TemporaryDirectory


FROZEN = Path(__file__).resolve().parent / "frozen"
BASELINES = "fgcs_baselines_20261118"
EARLIER = "fgcs_retirement_action_20261117"
REPLAY = "fgcs_configaware_20261119"


def score(args):
    command = [sys.executable, "-B", str(FROZEN / BASELINES / "score_kind.py"),
               "--evaluation", str(args.evaluation), "--output", str(args.output)]
    if args.calibration is not None:
        command.extend(["--calibration", str(args.calibration)])
    subprocess.run(command, check=True)


def replay(args):
    root = args.provenance_root.resolve()
    research = root / ".research"
    attestations = {
        BASELINES: ("KIND_PROTOCOL.md", "run_kind_baselines.py", "score_kind.py", "SCORING.md"),
        EARLIER: ("kind_base.py", "run_kind.py"),
    }
    missing = [str(Path(".research") / study / name) for study, files in attestations.items()
               for name in files if not (research / study / name).is_file()]
    if missing:
        raise SystemExit("Missing archived provenance files under --provenance-root: " + ", ".join(missing))

    # Run only the bundled Python program, never the private attestation files.
    # The historical reader must keep its archived relative SHA-256 receipt keys.
    run = runpy.run_path(str(FROZEN / REPLAY / "replay_config.py"))["run"]
    run.__globals__.update(ROOT=root, BACKUP=research / BASELINES, FROZEN=research / EARLIER)
    run(args.source.resolve(), args.output.resolve())


def test(_args):
    base = FROZEN / BASELINES
    scorer = runpy.run_path(str(base / "score_kind.py"))
    assignments = [(c, a) for c in scorer["CONDITIONS"] for a in scorer["ARMS"]]
    trials = [dict(trial_id=f"test-{block}-{order}", block=block, order=order, condition=c, arm=a)
              for block in range(1, 7) for order, (c, a) in enumerate(assignments, 1)]
    plan = dict(phase="evaluation", trials=trials, bind_tolerance_s=.05, window_s=8.5,
                decision_horizon_s=1., source_hashes={
                    name: scorer["checksum"](base / name) for name in ("SCORING.md", "score_kind.py")
                })
    with TemporaryDirectory() as directory:
        path = Path(directory)
        (path / "PLAN.json").write_text(json.dumps(plan))
        scored, pairs, groups, comparisons, manifest = scorer["score"](path)
    if ((len(scored), len(pairs), len(groups), len(comparisons)) != (48, 36, 8, 6)
            or any(manifest[k] for k in ("n_attempted", "n_bind_scored", "n_rows_with_anomalies"))
            or any(t["status"] != "not_run" or t["z_http_status"] != "not_run" for t in scored)
            or any(p["delta_signal_minus_baseline_y_arrival_to_bind_s"] is not None
                   or p["cost_direction"] != "not_scored" for p in pairs)):
        raise RuntimeError("Kind scoring changed the denominator or treated missing outcomes as measured")
    print("Kind scoring check passed: 48 planned trials and 36 pairs; missing outcomes remain unscored.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    scoring = commands.add_parser("score", help="score externally supplied physical evaluation receipts")
    scoring.add_argument("--evaluation", type=Path, required=True)
    scoring.add_argument("--calibration", type=Path)
    scoring.add_argument("--output", type=Path, required=True, help="new output directory")
    scoring.set_defaults(run=score)

    replaying = commands.add_parser("replay", help="replay first decisions, not latency or reservation cost")
    replaying.add_argument("--source", type=Path, required=True,
                           help="directory containing calibration/, evaluation/, and scored/")
    replaying.add_argument("--provenance-root", type=Path, required=True,
                           help="root of the externally supplied legacy code-and-data archive")
    replaying.add_argument("--output", type=Path, required=True, help="new output directory")
    replaying.set_defaults(run=replay)

    testing = commands.add_parser("test", help="run the included data-free unit test suite")
    testing.set_defaults(run=test)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
