"""Regenerate the Fig. 5 layout from the two verified RQ3 ledgers.

Panel (a) separates actual Z deletions from offline action predictions.
Panels (b) and (c) contain observed latency differences only.
"""

import argparse
from pathlib import Path
from statistics import mean
import subprocess

from reportlab.lib.colors import HexColor, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from plot_figure5 import CONDITIONS, collect, read_csv


LANG = {
    "en": {
        "observed": "Observed on Kind",
        "trials": "6 trials per setting and policy",
        "a": "(a) First decision",
        "b": "(b) Signal vs. pooled fixed",
        "c": "(c) Signal vs. Kubernetes",
        "deletions": "Actual Z deletions",
        "config": "Setting-specific fixed",
        "kubernetes": "Kubernetes",
        "fixed": "Pooled fixed",
        "signal": "Signal",
        "replay": "Offline replay only",
        "replay_result": "Z retirement decisions",
        "fast": "Fast X",
        "slow": "Slow X",
        "y": "Y Bind",
        "z": "Z HTTP",
        "mean": "Mean",
        "block": "Paired block",
        "axis": "Signal minus reference latency (s; negative is faster)",
    },
    "ko": {
        "observed": "Kind 실측",
        "trials": "각 설정·정책 6회",
        "a": "(a) 최초 결정",
        "b": "(b) 신호 대 통합 고정값",
        "c": "(c) 신호 대 Kubernetes",
        "deletions": "실제 Z 삭제",
        "config": "설정별 고정값",
        "kubernetes": "Kubernetes",
        "fixed": "통합 고정값",
        "signal": "신호",
        "replay": "사후 결정 재생",
        "replay_result": "Z 회수 선택",
        "fast": "빠른 X",
        "slow": "느린 X",
        "y": "Y Bind",
        "z": "Z HTTP",
        "mean": "평균",
        "block": "짝지은 블록",
        "axis": "신호 방식 지연 - 비교 방식 지연 (초, 음수면 신호 방식이 빠름)",
    },
}


def draw(output, lang, counts, differences):
    names = LANG[lang]
    c = canvas.Canvas(str(output), pagesize=(660, 248),
                      invariant=True, pageCompression=1)
    c.setTitle("RQ3: observed Z deletion and latency, with an offline decision replay")
    ink, secondary, grid, observed_bg, replay_bg, replay_border, y_color, z_color = map(
        HexColor, ("#17232e", "#435565", "#d8e0e5", "#e9f3f6", "#f3f4f6",
                   "#777f88", "#14566f", "#864317")
    )

    def text(value, x, y, size=13, anchor="left", color=ink, *, bold=False):
        font = ("Helvetica-Bold" if bold else "Helvetica") if lang == "en" else "UnDotum"
        width = pdfmetrics.stringWidth(value, font, size)
        actual_left = x - {"left": 0, "right": width, "center": width / 2}[anchor]
        if actual_left < 0 or actual_left + width > 660 or not 0 < y < 248:
            raise ValueError(f"clipped text: {value!r}")
        c.setFillColor(color)
        c.setFont(font, size)
        {"left": c.drawString, "right": c.drawRightString, "center": c.drawCentredString}[anchor](
            x, y, value)

    def line(x0, y0, x1, y1, *, color=grid, width=0.7):
        c.setStrokeColor(color)
        c.setLineWidth(width)
        c.line(x0, y0, x1, y1)

    def diamond(x, y, color, radius=4.5):
        c.setFillColor(color)
        c.setStrokeColor(white)
        c.setLineWidth(0.8)
        p = c.beginPath()
        p.moveTo(x - radius, y)
        p.lineTo(x, y + radius)
        p.lineTo(x + radius, y)
        p.lineTo(x, y - radius)
        p.close()
        c.drawPath(p, fill=1, stroke=1)

    def badge(number, x, y, observed):
        c.setFillColor((y_color if number else observed_bg) if observed else white)
        c.setStrokeColor((y_color if number else grid) if observed else replay_border)
        c.setLineWidth(0.8)
        c.roundRect(x - 18.5, y - 10, 37, 21, 3, fill=1, stroke=1)
        text(f"{number}/6", x, y - 4.4, 13, "center",
             white if number and observed else ink, bold=True)

    c.setFillColor(observed_bg)
    c.roundRect(7, 212, 646, 29, 4, fill=1, stroke=0)
    text(names["observed"], 15, 221, 15, color=y_color, bold=True)
    text(names["trials"], 645, 221, 12.5, "right", secondary)
    for key, x in (("a", 11), ("b", 221), ("c", 442)):
        text(names[key], x, 192, 15, bold=True)
    line(212, 54, 212, 202)
    line(433, 54, 433, 202)

    text(names["deletions"], 12, 169, 12, color=secondary)
    count_columns = (146, 187)
    for condition, x in zip(CONDITIONS, count_columns):
        text(names[condition], x, 151, 12.3, "center", bold=True)
    for arm, y in (("kubernetes", 129), ("fixed", 103), ("signal", 77)):
        text(names[arm], 12, y - 4.3, 13)
        for condition, x in zip(CONDITIONS, count_columns):
            badge(counts[arm, condition], x, y, observed=True)

    c.setFillColor(replay_bg)
    c.setStrokeColor(replay_border)
    c.setLineWidth(0.9)
    c.setDash([3, 2])
    c.roundRect(7, 5, 203, 55, 4, fill=1, stroke=1)
    c.setDash([])
    text(names["replay"], 13, 43, 13, bold=True)
    text(names["config"], 13, 25, 11.5)
    text(names["replay_result"], 13, 11, 10.3, color=secondary)
    for condition, x in zip(CONDITIONS, count_columns):
        badge(counts["config", condition], x, 24, observed=False)

    # The two measured comparisons share their units, scale and row ordering.
    # Vertical jitter separates overlapping blocks without changing x values.
    rows = (("fast", "y", 149), ("fast", "z", 125),
            ("slow", "y", 92), ("slow", "z", 68))
    xmin, xmax = -2.45, 3.40
    for reference, label_x, x0, x1, mean_x in (
        ("fixed", 221, 267, 373, 426), ("kubernetes", 442, 488, 594, 647)
    ):
        def location(value):
            return x0 + (value - xmin) / (xmax - xmin) * (x1 - x0)

        for tick in range(-2, 4):
            x = location(tick)
            line(x, 56, x, 161, color=secondary if tick == 0 else grid,
                 width=1 if tick == 0 else 0.6)
            text(str(tick), x, 42, 11.5, "center", secondary)
        text(names["mean"], mean_x, 170, 11.5, "right", secondary)
        for condition, y in (("fast", 168), ("slow", 106)):
            text(names[condition], label_x, y, 12, color=secondary, bold=True)
        for condition, key, y in rows:
            values = differences[reference, condition, key]
            if not all(xmin < value < xmax for value in values):
                raise ValueError(f"out-of-range points: {reference}/{condition}/{key}")
            color = y_color if key == "y" else z_color
            text(names[key], label_x, y - 4.3, 12, color=color)
            line(x0, y, x1, y, width=0.5)
            c.setFillColor(color)
            for i, value in enumerate(values):
                c.circle(location(value), y + (i - 2.5) * 2.8, 2.05, fill=1, stroke=0)
            avg = mean(values)
            diamond(location(avg), y, color)
            formatted = f"{avg:+.3f}" if round(avg, 3) else "0.000"
            text(formatted, mean_x, y - 4.7, 13, "right", color, bold=True)

    text(names["axis"], 435, 23, 12.2, "center")
    c.setFillColor(secondary)
    c.circle(365, 10, 2.2, fill=1, stroke=0)
    text(names["block"], 373, 6, 11.2, color=secondary)
    diamond(462, 10, secondary, 4.2)
    text(names["mean"], 470, 6, 11.2, color=secondary)
    c.showPage()
    c.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", type=Path, required=True, help="Verified first_decisions.csv")
    parser.add_argument("--pairs", type=Path, required=True, help="Verified block_pairs.csv")
    parser.add_argument("--out", type=Path, required=True, help="Target directory")
    parser.add_argument("--language", choices=("en", "ko", "both"), default="both")
    parser.add_argument("--korean-font", type=Path, help="UnDotum.ttf (otherwise uses kpsewhich)")
    args = parser.parse_args()
    languages = ("en", "ko") if args.language == "both" else (args.language,)
    if "ko" in languages:
        korean = args.korean_font
        if korean is None:
            try:
                found = subprocess.check_output(["kpsewhich", "UnDotum.ttf"], text=True).strip()
            except (OSError, subprocess.CalledProcessError):
                parser.error("Korean output requires UnDotum.ttf (provide --korean-font)")
            korean = Path(found)
        if not korean.is_file():
            parser.error(f"Korean font not found: {korean}")
        pdfmetrics.registerFont(TTFont("UnDotum", str(korean)))
    counts, differences = collect(read_csv(args.first), read_csv(args.pairs))
    args.out.mkdir(parents=True, exist_ok=True)
    for language in languages:
        draw(args.out / f"config-control-{language}.pdf", language, counts, differences)
    print("Verified deletion/replay counts:", counts)
    for key, pairs in differences.items():
        print(f"{key}: n={len(pairs)}, mean={mean(pairs):+.9f} s")


if __name__ == "__main__":
    main()
