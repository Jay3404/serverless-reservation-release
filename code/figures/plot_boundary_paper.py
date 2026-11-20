"""Render the paper-layout Figure 3 from recomputed boundary gains."""

import argparse
import csv
from pathlib import Path
from statistics import mean

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, fontManager


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gains", type=Path, required=True)
    parser.add_argument("--language", choices=("en","ko"), default="en")
    parser.add_argument("--korean-font", type=Path,
                        help="path to UnDotum.ttf")
    parser.add_argument("--output", type=Path, required=True, help="new destination PDF")
    args = parser.parse_args()
    ko = args.language == "ko"
    if ko:
        if args.korean_font is None:
            parser.error("--language ko requires --korean-font")
        fontManager.addfont(args.korean_font)
        plt.rcParams["font.family"] = [FontProperties(fname=args.korean_font).get_name(), "DejaVu Sans"]
    plt.rcParams.update({
        "font.size": 8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "grid.alpha": .23,
        "pdf.fonttype": 42,
    })
    with args.gains.open(newline="", encoding="utf-8") as source:
        data = [
            (int(float(row["cleanup_s"])), int(row["session"]),
             float(row["target_minus_cleanup_s"]), float(row["gain_ms"]))
            for row in csv.DictReader(source)
        ]
    assert len(data) == 36
    assert {(d, session, offset) for d, session, offset, _ in data} == {
        (d, session, offset)
        for d in (2, 5) for session in range(1, 7) for offset in (-.5, 0, .5)
    }

    fig, (arrivals, sessions) = plt.subplots(
        1, 2, figsize=(7.05, 2.55), layout="constrained", sharey=True,
    )
    colors, markers = {2: "#245779", 5: "#a04728"}, {2: "o", 5: "s"}
    for d, session, offset, gain in data:
        arrivals.scatter(offset + .022 * (session - 3.5) + (.014 if d == 5 else -.014),
                         gain, s=17, marker=markers[d], color=colors[d], alpha=.72, zorder=3)
        sessions.scatter(session + .13 * offset + (.043 if d == 5 else -.043),
                         gain, s=17, marker=markers[d], color=colors[d], alpha=.72, zorder=3)
    for session in range(1, 7):
        gains = [gain for _, current, _, gain in data if current == session]
        sessions.scatter(session, mean(gains), marker="_", s=120, color="#252525",
                         linewidths=2, zorder=4)
    for axis in (arrivals, sessions):
        axis.axhline(0, lw=.65, color="#555555", zorder=2)
        axis.set_ylim(-32, 970)
    arrivals.set(xticks=[-.5, 0, .5], xticklabels=[r"$-0.5$" if ko else "−0.5", "0", "+0.5"],
                 xlabel=r"목표 도착 오프셋 $-$ 정리 시간 (초)" if ko else "Assigned arrival minus cleanup duration (s)",
                 ylabel="절대오차 짝차이 (ms)" if ko else "Paired error gain (ms)",
                 title="(a) 배정 도착 오프셋" if ko else "(a) Assigned arrival offset")
    sessions.set(xticks=range(1, 7), xlabel="평가 세션 (같은 호스트)" if ko else "Evaluation session",
                 title="(b) 평가 세션" if ko else "(b) Session")
    for d in (2, 5):
        arrivals.scatter([], [], s=17, marker=markers[d], color=colors[d],
                         label=f"정리 {d}초" if ko else f"{d} s cleanup")
    sessions.scatter([], [], marker="_", s=95, color="#252525", label="세션 평균" if ko else "Session mean")
    for axis in (arrivals, sessions):
        axis.legend(loc="upper left", frameon=False)
    with args.output.open("xb") as f:
        fig.savefig(f, format="pdf", metadata={
            "Title": "자연 경계 요청의 배치 예측 절대오차 짝차이" if ko else "Paired boundary forecast error gains"})


if __name__ == "__main__":
    main()
