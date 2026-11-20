"""Render paired RQ3 latency in the 48-trial control evaluation as vector PDFs."""

import argparse
import csv
from itertools import product
from math import isclose, isfinite
from pathlib import Path
from statistics import mean
import subprocess

from reportlab.lib.colors import HexColor, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


LANG = {
    "en": dict(
        a="(a) First Z retirement", b="(b) Signal vs. pooled fixed",
        c="(c) Signal vs. Kubernetes", fast="Fast X", slow="Slow X",
        kubernetes="Kubernetes (observed)", fixed="Pooled fixed (observed)",
        signal="Signal (observed)", config="Setting-specific fixed (replay)",
        y="Y Bind", z="Z HTTP", row="Mean", legend="Paired block", avg="Mean",
        axis="Signal minus reference latency (s)",
    ),
    "ko": dict(
        a="(a) Z 최초 회수", b="(b) 신호와 통합 고정값 비교",
        c="(c) 신호와 Kubernetes 비교", fast="빠른 X", slow="느린 X",
        kubernetes="Kubernetes (실측)", fixed="통합 고정값 (실측)",
        signal="신호 (실측)", config="설정별 고정값 (재생)",
        y="Y 배치", z="Z HTTP", row="평균", legend="블록별 짝차이", avg="평균",
        axis="신호 방식의 지연 - 비교 방식의 지연 (초)",
    ),
}
CONDITIONS = ("fast", "slow")
ARMS = ("fixed", "instant", "kubernetes", "signal")
BLOCKS = tuple(str(i) for i in range(1, 7))
ENDPOINTS = {"y": "y_arrival_to_bind_s", "z": "z_http_response_s"}


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def collect(first, pairs):
    """Validate the recorded pairing and distinguish deletions from replay decisions."""
    first_by_id = {r["trial_id"]: r for r in first}
    first_keys = {(r["condition"], r["original_arm"], r["block"]) for r in first}
    if len(first) != 48 or len(first_by_id) != 48 or first_keys != set(product(CONDITIONS, ARMS, BLOCKS)):
        raise ValueError("expected exactly one first decision per condition/arm/block")
    pod_uids = [r[f"{app}_UID"] for r, app in product(first, ("X", "Y", "Z"))]
    if not all(pod_uids) or len(set(pod_uids)) != len(pod_uids):
        raise ValueError("first-decision observations must have unique X/Y/Z Pod UIDs")
    pair_keys = {(r["condition"], r["baseline_arm"], r["block"]) for r in pairs}
    if len(pairs) != 36 or pair_keys != set(product(CONDITIONS, ARMS[:-1], BLOCKS)):
        raise ValueError("expected 36 complete comparison rows")

    counts, differences = {}, {}
    for condition, reference in product(CONDITIONS, ("fixed", "kubernetes")):
        selected = [r for r in pairs if (r["condition"], r["baseline_arm"]) == (condition, reference)]
        selected.sort(key=lambda r: int(r["block"]))
        deletes = {"baseline": [], "signal": []}
        config = []
        for pair in selected:
            for role, arm in (("baseline", reference), ("signal", "signal")):
                trial = first_by_id[pair[f"{role}_trial_id"]]
                if (trial["condition"], trial["original_arm"], trial["block"]) != (condition, arm, pair["block"]):
                    raise ValueError(f"mispaired trial {trial['trial_id']}")
                observed = pair[f"{role}_first_delete_confirmed"]
                if pair[f"{role}_status"] != "complete" or observed not in ("True", "False"):
                    raise ValueError(f"unscorable deletion status for {trial['trial_id']}")
                if observed != trial["original_first_z_delete_applied"]:
                    raise ValueError(f"deletion ledger mismatch for {trial['trial_id']}")
                deletes[role].append(observed == "True")
                if role == "signal":
                    reconstructed = trial["config_first_action_guarded_replay"]
                    if reconstructed not in ("wait", "reclaim"):
                        raise ValueError(f"unscorable first-action replay for {trial['trial_id']}")
                    config.append(reconstructed == "reclaim")
        counts[reference, condition] = sum(deletes["baseline"])
        for arm, value in (("signal", sum(deletes["signal"])), ("config", sum(config))):
            if counts.setdefault((arm, condition), value) != value:
                raise ValueError("signal cohort differs across comparisons")

        for key, endpoint in ENDPOINTS.items():
            values = []
            for pair in selected:
                value = float(pair[f"delta_signal_minus_baseline_{endpoint}"])
                check = float(pair[f"signal_{endpoint}"]) - float(pair[f"baseline_{endpoint}"])
                if not isfinite(value) or not isclose(value, check, rel_tol=1e-11, abs_tol=1e-12):
                    raise ValueError(f"paired endpoint mismatch: {pair['signal_trial_id']} {endpoint}")
                values.append(value)
            differences[reference, condition, key] = values
    return counts, differences


def draw(path, lang, font, counts, differences):
    names = LANG[lang]
    c = canvas.Canvas(str(path), pagesize=(700, 201), invariant=True, pageCompression=1)
    c.setTitle("RQ3: Z retirement and paired latency effects")
    ink, subdued, rule, paper, y_color, z_color = map(
        HexColor, ("#182632", "#566574", "#d5dce2", "#edf2f5", "#1d5774", "#91502c"))

    def label(value, x, y, size=11, anchor="left", color=ink, bold=False):
        c.setFillColor(color)
        c.setFont("Helvetica-Bold" if lang == "en" and bold else font, size)
        {"left": c.drawString, "center": c.drawCentredString, "right": c.drawRightString}[anchor](x, y, value)

    def line(x0, y0, x1, y1, color=rule, width=0.55):
        c.setStrokeColor(color)
        c.setLineWidth(width)
        c.line(x0, y0, x1, y1)

    def diamond(x, y, color, radius=4):
        c.setFillColor(color)
        c.setStrokeColor(white)
        c.setLineWidth(0.65)
        p = c.beginPath()
        p.moveTo(x - radius, y)
        p.lineTo(x, y + radius)
        p.lineTo(x + radius, y)
        p.lineTo(x, y - radius)
        p.close()
        c.drawPath(p, fill=1, stroke=1)

    label(names["a"], 11, 183, 13, bold=True)
    label(names["b"], 242, 183, 13, bold=True)
    label(names["c"], 489, 183, 13, bold=True)
    line(226, 38, 226, 186)
    line(482, 38, 482, 186)

    for condition, x in zip(CONDITIONS, (163, 200)):
        label(names[condition], x, 153, 10.5, "center", bold=True)
    for arm, y in (("kubernetes", 134), ("fixed", 109), ("signal", 84), ("config", 59)):
        label(names[arm], 11, y - 3.8, 10.3)
        for condition, x in zip(CONDITIONS, (163, 200)):
            n = counts[arm, condition]
            replay = arm == "config"
            c.setFillColor(white if replay else y_color if n else paper)
            c.setStrokeColor(y_color if n else rule)
            c.setLineWidth(0.8)
            c.setDash([2.5, 1.7] if replay else [])
            c.roundRect(x - 15, y - 8.7, 30, 18, 3.5, fill=1, stroke=1)
            c.setDash([])
            label(f"{n}/6", x, y - 3.65, 10.4, "center", white if n and not replay else ink, True)

    for condition, y in (("fast", 156), ("slow", 100)):
        label(names[condition], 242, y, 10.3, color=subdued, bold=True)
    ordered_rows = tuple(product(CONDITIONS, ENDPOINTS))
    rows_y = (137, 115, 81, 59)
    for (_, key), axis_y in zip(ordered_rows, rows_y):
        label(names[key], 242, axis_y - 3.7, 10.7)
    xmin, xmax = -2.45, 3.40
    for reference, x0, x1, avg_x in (("fixed", 298, 436, 475), ("kubernetes", 503, 641, 686)):
        def location(value):
            return x0 + (value - xmin) / (xmax - xmin) * (x1 - x0)

        label(names["row"], avg_x, 156, 10, "right", subdued)
        for tick in range(-2, 4):
            x = location(tick)
            line(x, 44, x, 150, subdued if tick == 0 else rule, 0.9 if tick == 0 else 0.55)
            label(str(tick), x, 32, 9, "center", subdued)
        for (condition, key), axis_y in zip(ordered_rows, rows_y):
            arr = differences[reference, condition, key]
            if not all(xmin < val < xmax for val in arr):
                raise ValueError(f"axis clipping: {reference}/{condition}/{key}")
            color = y_color if key == "y" else z_color
            line(x0, axis_y, x1, axis_y, rule, 0.42)
            c.setFillColor(color)
            for i, val in enumerate(arr):
                c.circle(location(val), axis_y + (i - 2.5) * 2.4, 1.7, fill=1, stroke=0)
            avg = mean(arr)
            diamond(location(avg), axis_y, color)
            displayed = f"{avg:+.3f}" if round(avg, 3) else "0.000"
            label(displayed, avg_x, axis_y - 3.7, 10.8, "right", color, True)

    label(names["axis"], 469, 12, 10.7, "center")
    c.setFillColor(subdued)
    c.circle(245, 15.5, 1.8, fill=1, stroke=0)
    label(names["legend"], 251, 12, 9.1, color=subdued)
    diamond(598, 15.5, subdued, radius=3.3)
    label(names["avg"], 604, 12, 9.1, color=subdued)
    c.showPage()
    c.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", type=Path, required=True, help="Verified first_decisions.csv ledger")
    parser.add_argument("--pairs", type=Path, required=True, help="Verified block_pairs.csv ledger")
    parser.add_argument("--out", type=Path, required=True, help="Directory for generated PDF figures")
    parser.add_argument("--language", choices=("en", "ko", "both"), default="en")
    parser.add_argument("--korean-font", type=Path, help="UnDotum.ttf for Korean figure labels")
    args = parser.parse_args()
    languages = ("en", "ko") if args.language == "both" else (args.language,)
    if "ko" in languages:
        korean = args.korean_font
        if korean is None:
            try:
                result = subprocess.run(
                    ["kpsewhich", "UnDotum.ttf"], check=True, capture_output=True, text=True
                )
                korean = Path(result.stdout.strip())
            except (OSError, subprocess.CalledProcessError):
                parser.error("Korean output requires --korean-font /path/to/UnDotum.ttf or a TeX installation with UnDotum")
        if not korean.is_file():
            parser.error(f"Korean font not found: {korean}")
        pdfmetrics.registerFont(TTFont("Korean", str(korean)))
    counts, differences = collect(read_csv(args.first), read_csv(args.pairs))
    args.out.mkdir(parents=True, exist_ok=True)
    for language in languages:
        draw(args.out / f"config-control-{language}.pdf", language,
             "Helvetica" if language == "en" else "Korean", counts, differences)
    print(f"Validated first-decision counts: {counts}")
    for key, pair_values in differences.items():
        print(f"{key}: n={len(pair_values)}; mean={mean(pair_values):+.9f} s")


if __name__ == "__main__":
    main()
