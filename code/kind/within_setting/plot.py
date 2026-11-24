"""Render the optional bilingual Fig. 6 from the audited evaluation tables."""
from __future__ import annotations

import argparse
import csv
from itertools import product
import math
from pathlib import Path
from statistics import mean
import subprocess

from reportlab.lib.colors import HexColor, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from run import ARMS, DISTURBANCES, SETTINGS, schedule


ENDPOINTS = ("y_bind_s", "z_http_s")
ROWS = tuple(product(SETTINGS, DISTURBANCES))
BLOCKS = range(1, 5)
LANG = {
    "en": {
        "observed": "Physical control experiment on Kind",
        "trial_count": "{done}/{planned} assigned trials complete",
        "actions": "(a) Actual first Z deletions",
        "y_bind_s": "(b) Y arrival to Bind",
        "z_http_s": "(c) Z HTTP response",
        "row": "Cleanup / injected delay (s)",
        "pooled": "Pooled",
        "setting": "By setting",
        "signal": "Signal",
        "mean": "Mean",
        "point": "Paired block",
        "axis": "Signal minus setting-specific fixed (s; negative is faster)",
        "dnote": "Deletions / recorded first actions",
        "planned": "4 planned trials per arm and row",
        "nnote": "Means show the number of scored pairs (of 4)",
    },
    "ko": {
        "observed": "Kind 실제 제어 실험",
        "trial_count": "배정 {planned}회 중 {done}회 완료",
        "actions": "(a) 최초 결정에서 실제 Z 삭제",
        "y_bind_s": "(b) Y 도착부터 Bind",
        "z_http_s": "(c) Z HTTP 응답",
        "row": "정리 / 주입 지연 (초)",
        "pooled": "통합 고정",
        "setting": "설정별 고정",
        "signal": "신호",
        "mean": "평균",
        "point": "짝지은 블록",
        "axis": "신호 방식 - 설정별 고정값 (초, 음수면 신호 방식이 빠름)",
        "dnote": "실제 삭제 / 최초 행동이 기록된 회차",
        "planned": "각 행에서 정책별 4회 배정",
        "nnote": "평균 옆 괄호: 짝비교 가능 횟수 (4회 중)",
    },
}


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def require(condition, error):
    if not condition:
        raise ValueError(error)


def boolean(value):
    require(value in ("True", "False"), f"expected audit boolean, got {value!r}")
    return value == "True"


def number(value):
    parsed = float(value)
    require(math.isfinite(parsed), f"non-finite audit value: {value!r}")
    return parsed


def collect(trial_rows, pair_rows):
    """Check exact blocked assignments and rederive Signal minus Fixed deltas."""
    expected = {r["trial_id"]: r for r in schedule("evaluation")}
    trials = {r["trial_id"]: r for r in trial_rows}
    require(len(trial_rows) == len(trials) and trials.keys() == expected.keys(),
            "trials.csv does not contain every sealed assignment exactly once")
    valid_status = {"complete", "incomplete", "unattempted"}
    trial_keys = {}
    for trial_id, r in trials.items():
        assigned = expected[trial_id]
        require(all(r[k] == str(assigned[k]) for k in ("condition", "disturbance", "block", "order", "arm")),
                f"sealed blocked assignment changed for {trial_id}")
        require(r["status"] in valid_status, f"unknown trial status for {trial_id}")
        require(r["status"] != "complete" or r.get("first_deleted") in ("True", "False"),
                f"complete trial missing first-action record: {trial_id}")
        trial_keys[r["condition"], r["disturbance"], int(r["block"]), r["arm"]] = r

    pair_keys = {}
    written = {(*row, b, comparator)
               for row, b, comparator in product(ROWS, BLOCKS, ("pooled", "setting"))}
    for pair in pair_rows:
        key = pair["setting"], pair["disturbance"], int(pair["block"]), pair["comparator"]
        require(key in written and key not in pair_keys, f"unexpected or duplicate pair {key}")
        pair_keys[key] = pair
    require(pair_keys.keys() == written, "pairs.csv does not contain every sealed pairing")

    counts = {}
    values = {}
    for setting, disturbance in ROWS:
        for arm in ARMS:
            outcomes = [trial_keys[setting, disturbance, b, arm].get("first_deleted")
                        for b in BLOCKS]
            observed = [boolean(v) for v in outcomes if v not in (None, "")]
            counts[setting, disturbance, arm] = sum(observed), len(observed)
        for block, comparator in product(BLOCKS, ("pooled", "setting")):
            pair = pair_keys[setting, disturbance, block, comparator]
            sr = trial_keys[setting, disturbance, block, "signal"]
            fr = trial_keys[setting, disturbance, block, comparator]
            require((sr["trial_id"], fr["trial_id"]) == (pair["signal_trial"], pair["fixed_trial"]),
                    f"mispaired trial IDs for {setting}/{disturbance}/{block}/{comparator}")
            status = sr["status"] == fr["status"] == "complete"
            require(boolean(pair["complete_pair"]) == status,
                    f"complete-pair status changed: {pair['signal_trial']}/{pair['fixed_trial']}")
            for r, arm in ((sr, "signal"), (fr, "fixed")):
                require((r.get("first_deleted") or "") == (pair.get(f"{arm}_first_deleted") or ""),
                        f"mismatched first deletion: {r['trial_id']}")
            for endpoint in (*ENDPOINTS, "y_http_s"):
                if not status:
                    require(not pair.get(endpoint),
                            f"unexpected latency in incomplete pair {pair['signal_trial']}/{pair['fixed_trial']}")
                    continue
                delta = number(sr[endpoint]) - number(fr[endpoint])
                require(math.isclose(delta, number(pair[endpoint]), rel_tol=1e-12, abs_tol=1e-12),
                        f"pair sign or value changed for {endpoint}: {pair['signal_trial']}")
                if endpoint in ENDPOINTS and comparator == "setting":
                    values.setdefault((setting, disturbance, endpoint), []).append((block, delta))
    complete = sum(r["status"] == "complete" for r in trial_rows)
    require(any(values.values()), "no jointly scorable Signal vs. Setting-specific Fixed pairs to plot")
    return counts, values, complete


def limits(values):
    largest = max(abs(v) for rows in values.values() for _, v in rows)
    target = max(largest / 2.0, 0.001)
    base = 10 ** math.floor(math.log10(target))
    step = next(k * base for k in (1, 2, 5, 10) if k * base >= target)
    return 2.25 * step, (-2 * step, -step, 0.0, step, 2 * step)


def draw(path, lang, counts, values, complete):
    names = LANG[lang]
    c = canvas.Canvas(str(path), pagesize=(700, 240), invariant=True, pageCompression=1)
    c.setTitle("Within-setting RQ3: observed decisions and paired outcomes")
    ink, muted, grid, tint, y_colour, z_colour = map(
        HexColor, ("#17232e", "#475964", "#d7dfe4", "#e8f3f6", "#115774", "#8b461e"))

    def text(s, x, y, size=13, anchor="left", colour=ink, *, bold=False):
        font = ("Helvetica-Bold" if bold else "Helvetica") if lang == "en" else "UnDotum"
        w = pdfmetrics.stringWidth(s, font, size)
        left = x - {"left": 0, "center": w / 2, "right": w}[anchor]
        require(0 <= left <= left + w <= 700 and 0 < y < 240, f"clipping text: {s}")
        c.setFillColor(colour)
        c.setFont(font, size)
        {"left": c.drawString, "center": c.drawCentredString, "right": c.drawRightString}[anchor](x, y, s)

    def line(x1, y1, x2, y2, colour=grid, width=0.7):
        c.setStrokeColor(colour)
        c.setLineWidth(width)
        c.line(x1, y1, x2, y2)

    def diamond(x, y, colour, radius=4.5):
        p = c.beginPath()
        p.moveTo(x - radius, y)
        p.lineTo(x, y + radius)
        p.lineTo(x + radius, y)
        p.lineTo(x, y - radius)
        p.close()
        c.setFillColor(colour)
        c.setStrokeColor(white)
        c.setLineWidth(0.7)
        c.drawPath(p, fill=1, stroke=1)

    c.setFillColor(tint)
    c.roundRect(7, 206, 686, 28, 4, fill=1, stroke=0)
    text(names["observed"], 15, 214.5, 15, colour=y_colour, bold=True)
    text(names["trial_count"].format(done=complete, planned=48), 685, 214.5, 12.5, "right", muted)
    text(names["actions"], 12, 184, 14.5, bold=True)
    text(names["row"], 12, 162, 11.5, colour=muted)
    col = dict(zip(ARMS, (113, 176, 235)))
    for arm, x in col.items():
        text(names[arm], x, 144, 11.8, "center", muted, bold=True)
    y_rows = (123, 99, 75, 51)
    for (setting, disturbance), y in zip(ROWS, y_rows):
        text(f"{SETTINGS[setting]:.1f} / {DISTURBANCES[disturbance]:.2f}", 12, y - 4.2, 13)
        for arm, x in col.items():
            deletes, seen = counts[setting, disturbance, arm]
            c.setFillColor(y_colour if deletes else tint)
            c.setStrokeColor(y_colour if deletes else grid)
            c.setLineWidth(0.7)
            c.roundRect(x - 20, y - 9, 40, 18, 3, fill=1, stroke=1)
            text(f"{deletes}/{seen}" if seen else "NA", x, y - 4.1, 12,
                 "center", white if deletes else ink, bold=True)
    text(names["dnote"], 12, 20, 11, colour=muted)
    text(names["planned"], 12, 6, 11, colour=muted)

    bound, ticks = limits(values)
    line(267, 41, 267, 194)
    line(483, 41, 483, 151)
    line(483, 175, 483, 194)
    for endpoint, x0, x1, xm, colour in (
        ("y_bind_s", 280, 408, 474, y_colour), ("z_http_s", 496, 624, 690, z_colour)
    ):
        def location(v):
            return x0 + (v + bound) / (2 * bound) * (x1 - x0)

        text(names[endpoint], x0, 184, 14.5, bold=True)
        text(names["mean"], xm, 144, 11.5, "right", muted)
        for tick in ticks:
            x = location(tick)
            line(x, 39, x, 137, muted if not tick else grid, 1 if not tick else 0.6)
            text(f"{tick:g}", x, 26, 10.7, "center", muted)
        for row, y in zip(ROWS, y_rows):
            arr = values.get((*row, endpoint), [])
            line(x0, y, x1, y, width=0.5)
            c.setFillColor(colour)
            for block, val in arr:
                require(abs(val) < bound, "plot width does not contain value")
                c.circle(location(val), y + (block - 2.5) * 3.5, 2, fill=1, stroke=0)
            if arr:
                avg = mean(v for _, v in arr)
                diamond(location(avg), y, colour)
                shown = f"{avg:+.3f}" if round(avg, 3) else "0.000"
                text(f"{shown} ({len(arr)})", xm, y - 4.5, 12.3, "right", colour, bold=True)
            else:
                text("NA (0)", xm, y - 4.5, 12.3, "right", muted)
    text(names["axis"], 484, 11, 11.7, "center")
    c.setFillColor(muted)
    c.circle(311, 164.5, 2, fill=1, stroke=0)
    text(names["point"], 318, 161, 11, colour=muted)
    diamond(414, 164.5, muted, 4)
    text(names["nnote"], 422, 161, 11, colour=muted)
    c.showPage()
    c.save()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trials", type=Path, required=True, help="Scorer's trials.csv")
    p.add_argument("--pairs", type=Path, required=True, help="Scorer's pairs.csv")
    p.add_argument("--out", type=Path, required=True, help="Output directory")
    p.add_argument("--language", choices=("en", "ko", "both"), default="both")
    p.add_argument("--korean-font", type=Path, help="Path to UnDotum.ttf (default via kpsewhich)")
    args = p.parse_args()
    languages = ("en", "ko") if args.language == "both" else (args.language,)
    if "ko" in languages:
        korean = args.korean_font
        if korean is None:
            try:
                korean = Path(subprocess.check_output(["kpsewhich", "UnDotum.ttf"], text=True).strip())
            except (OSError, subprocess.CalledProcessError):
                p.error("could not locate UnDotum.ttf; pass --korean-font")
        if not korean.is_file():
            p.error(f"cannot read Korean font {korean}")
        pdfmetrics.registerFont(TTFont("UnDotum", str(korean)))
    counts, values, complete = collect(read_csv(args.trials), read_csv(args.pairs))
    args.out.mkdir(parents=True, exist_ok=True)
    paths = [(lang, args.out / f"within-setting-{lang}.pdf") for lang in languages]
    require(not any(path.exists() for _, path in paths), "refusing to overwrite an existing figure")
    for lang, path in paths:
        draw(path, lang, counts, values, complete)
        print(path)


if __name__ == "__main__":
    main()
