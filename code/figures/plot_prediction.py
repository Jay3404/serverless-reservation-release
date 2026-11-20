#!/usr/bin/env python3
"""Draw the paper's schematics and prediction figures (Figures 1–4).

Run code/prediction/reproduce.py to recalculate the plotting inputs.
Dependencies: Python 3.10+ and ReportLab. Optional --font-dir points to
DejaVuSans.ttf and DejaVuSans-Bold.ttf; otherwise use local discovery/Helvetica.
"""

import argparse
import csv
import math
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean

from reportlab.lib.colors import Color, HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

WIDTH = 470
INK = HexColor('#202833')
SUB = HexColor('#4A5562')
RULE = HexColor('#B7C0CB')
GRID = HexColor('#DFE4EA')
NEUTRAL = HexColor('#F5F7F9')
BLUE = HexColor('#285779')
PALE_BLUE = HexColor('#E9F1F7')
ORANGE = HexColor('#8A492E')
PALE_ORANGE = HexColor('#F7EEE8')
GREEN = HexColor('#355947')
PALE_GREEN = HexColor('#EAF2EC')
WHITE = HexColor('#FFFFFF')
FONT, BOLD = 'Helvetica', 'Helvetica-Bold'


def register_fonts(explicit):
    global FONT, BOLD
    dirs = [explicit] if explicit else [
        Path('/usr/share/fonts/truetype/dejavu'),
        Path('/usr/local/share/fonts/dejavu'),
    ]
    for directory in dirs:
        normal, bold = directory / 'DejaVuSans.ttf', directory / 'DejaVuSans-Bold.ttf'
        if normal.is_file() and bold.is_file():
            pdfmetrics.registerFont(TTFont('Figure Sans', str(normal)))
            pdfmetrics.registerFont(TTFont('Figure Sans Bold', str(bold)))
            FONT, BOLD = 'Figure Sans', 'Figure Sans Bold'
            return
    if explicit:
        raise ValueError(f'Missing DejaVu Sans files in {explicit}')


def new(out, name, height, title):
    canvas = Canvas(str(out / f'{name}.pdf'), pagesize=(WIDTH, height), invariant=1, pageCompression=1)
    canvas.setTitle(title)
    canvas.setCreator('FGCS retirement manuscript figure generator')
    return canvas


def tx(c, x, y, value, size=9, *, color=INK, bold=False, align='left'):
    c.setFillColor(color)
    c.setFont(BOLD if bold else FONT, size)
    getattr(c, {'left':'drawString', 'right':'drawRightString', 'center':'drawCentredString'}[align])(x, y, value)


def multi(c, x, y, values, size=9, *, color=INK, bold=False, align='left', line_height=12):
    for i, item in enumerate(values):
        tx(c, x, y - i * line_height, item, size, color=color, bold=bold, align=align)


def centered_notation(c, x, y, label, size, *, color=INK, bold=False):
    """Render t_0 as a typographic subscript in any of the configured fonts."""
    parts = label.split('t_0')
    runs = []
    for index, part in enumerate(parts):
        if index:
            runs.extend([('t', size, 0), ('0', size*.75, -size*.1875)])
        runs.append((part, size, 0))
    widths = [pdfmetrics.stringWidth(s, BOLD if bold else FONT, fs) for s, fs, _ in runs]
    label_x = x - sum(widths)/2
    for (s, fs, shift), width in zip(runs, widths):
        tx(c, label_x, y+shift, s, fs, color=color, bold=bold)
        label_x += width


def line(c, x, y, ex, ey, *, color=RULE, width=.8, dash=None):
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.setDash(dash or [])
    c.line(x, y, ex, ey)
    c.setDash([])


def poly(c, points, fill, stroke=None, width=.9):
    p = c.beginPath()
    p.moveTo(*points[0])
    for xy in points[1:]:
        p.lineTo(*xy)
    p.close()
    c.setFillColor(fill)
    c.setStrokeColor(stroke or fill)
    c.setLineWidth(width)
    c.drawPath(p, fill=1, stroke=int(stroke is not None))


def arrow(c, x, y, ex, ey, *, color=SUB, width=.9, dash=None):
    line(c, x, y, ex, ey, color=color, width=width, dash=dash)
    angle = math.atan2(ey - y, ex - x)
    q = 4.5
    poly(c, [(ex, ey), (ex-q*math.cos(angle-.50), ey-q*math.sin(angle-.50)),
            (ex-q*math.cos(angle+.50), ey-q*math.sin(angle+.50))], color)


def box(c, x, y, w, h, fill=NEUTRAL, stroke=RULE, radius=4):
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.setLineWidth(.85)
    c.roundRect(x, y, w, h, radius, fill=1, stroke=1)


def dot(c, x, y, r, color, *, fill=True):
    c.setFillColor(color if fill else WHITE)
    c.setStrokeColor(color)
    c.setLineWidth(.9)
    c.circle(x, y, r, stroke=1, fill=1)


def timeline(out):
    c = new(out, 'timeline', 225, 'Cross-application retirement and placement timeline')
    tx(c, 5, 213, 'HTTP placement under memory contention', 11.5, bold=True)
    tx(c, 465, 214, 'schematic; not to scale', 8.1, align='right', color=SUB)
    x0, h, done, exit_x, cache_lo, cache_hi = 101, 156, 231, 285, 323, 343
    a, s, b, ready, response = 176, 222, 363, 402, 441
    yx, yy = 157, 70
    arrow(c, 72, yx, 463, yx)
    arrow(c, 72, yy, 463, yy)
    tx(c, 5, yx+9, 'Application X', 8.6, bold=True)
    tx(c, 5, yx-4, 'retiring Pod', 8.1, color=SUB)
    tx(c, 5, yy+9, 'Application Y', 8.6, bold=True)
    tx(c, 5, yy-4, 'arriving HTTP', 8.1, color=SUB)

    # Thick bar stops at the start of the interval, not a claimed exact release.
    box(c, x0, yx-7, cache_lo-x0, 14, PALE_BLUE, BLUE, radius=2)
    tx(c, (x0+cache_lo)/2+22, yx-3, 'X memory reservation retained', 8.5, color=BLUE, align='center', bold=True)
    c.setFillColor(PALE_ORANGE)
    c.rect(cache_lo, yx-9, cache_hi-cache_lo, 18, fill=1, stroke=0)
    for x in (cache_lo, cache_hi):
        line(c, x, yy+5, x, yx+18, color=ORANGE, dash=[2, 2])
    multi(c, cache_hi+6, 191, ['Cache removal', 'observation interval'], 8.1, color=ORANGE, bold=True, line_height=10)
    tx(c, x0, 194, 'Retirement', 8, align='center')
    # Use a typographic subscript even when the fallback font lacks U+2080.
    intent = [('intent (t', 8, 0), ('0', 6, -1.5), (')', 8, 0)]
    intent_widths = [pdfmetrics.stringWidth(label, FONT, size) for label, size, _ in intent]
    label_x = x0 - sum(intent_widths)/2
    for (label, size, shift), width in zip(intent, intent_widths):
        tx(c, label_x, 184+shift, label, size)
        label_x += width
    multi(c, h, 194, ['Handler', 'entry (h)'], 8, align='center', line_height=10)
    multi(c, done, 194, ['Cleanup', 'complete'], 8, align='center', line_height=10)
    multi(c, exit_x, 194, ['Process', 'exit'], 8, align='center', line_height=10)
    for x, color in ((x0, BLUE), (h, BLUE)):
        line(c, x, yx+7, x, 176, color=color)
        dot(c, x, yx, 2, color)
    for x in (done, exit_x):
        line(c, x, yx+7, x, 176, color=SUB)
        dot(c, x, yx+7, 1.7, SUB)

    line(c, s, yy+5, s, yx-10, color=GREEN, dash=[3, 2])
    multi(c, s-5, 114, ['Use only verified signal', 'delivered by this point'],
          7.3, align='right', color=SUB, line_height=10)
    line(c, s+3, yy+10, cache_lo, yy+10, color=ORANGE, width=1.8)
    line(c, cache_lo, yy+10, cache_hi, yy+10, color=ORANGE, dash=[2, 2])
    failed_attempt = 271
    dot(c, failed_attempt, yy+10, 2.8, ORANGE)
    line(c, failed_attempt, yy+12, failed_attempt, yy+25, color=ORANGE)
    multi(c, failed_attempt, yy+43, ['Scheduling attempt:', 'insufficient memory'],
          7.4, color=ORANGE, align='center', bold=True, line_height=10)

    events = [(a, ['HTTP', 'arrival (a)'], SUB),
              (s, ['Pre-create', 'cutoff (s)'], GREEN), (b, ['Bind (b)'], BLUE),
              (ready, ['Pod', 'Ready'], SUB), (response, ['HTTP', 'response'], SUB)]
    for x, labels, color in events:
        dot(c, x, yy, 2.4, color)
        multi(c, x, yy-15, labels, 8, color=color, align='center', bold=x in (s, b), line_height=10)
    for x in (a, b):
        line(c, x, 28, x, 36, color=BLUE)
    line(c, a, 28, b, 28, color=BLUE, width=1)
    tx(c, (a+b)/2, 14, 'Forecast target: HTTP arrival to Bind', 8.5, color=BLUE, align='center', bold=True)
    c.save()


def model(out):
    c = new(out, 'model', 244, 'Release estimators at the pre-create cutoff and shared Bind prediction')
    tx(c, 5, 232, 'What is available at the pre-create cutoff (s)?', 11.3, bold=True)
    box(c, 5, 185, 460, 33, PALE_GREEN, GREEN)
    centered_notation(c, 235, 204, 'Known: retirement intent t_0, cleanup d, preassigned capacity (contended/spare)',
                      8.9, bold=True, color=GREEN)
    tx(c, 235, 191, 'Optional: X handler entry h (same Pod UID, received, verified and delivered by s)',
       8.2, align='center', color=GREEN)

    arrow(c, 235, 185, 235, 172)
    tx(c, 12, 158, 'Release estimator', 9.1, bold=True)
    tx(c, 222, 158, 'Verified signal by s', 9.1, align='center', bold=True)
    tx(c, 382, 158, 'No verified signal by s', 9.1, align='center', bold=True)
    fixed = 't_0 + calibrated delay'
    signal = 'h + d + calibrated residual'
    options = [
        ('Calibrated Fixed', fixed, fixed, BLUE, PALE_BLUE),
        ('Signal + Configured', signal, 't_0 + d', ORANGE, PALE_ORANGE),
        ('Signal + Calibrated', signal, fixed, GREEN, PALE_GREEN),
    ]
    for y, (title, available, unavailable, accent, pale) in zip((134, 112, 90), options):
        box(c, 5, y-7, 132, 17, pale, accent, radius=3)
        tx(c, 12, y-1, title, 8.8, color=accent, bold=True)
        centered_notation(c, 222, y-1, available, 8.6)
        centered_notation(c, 382, y-1, unavailable, 8.6)
        line(c, 145, y-10, 463, y-10, color=GRID, width=.55)
    arrow(c, 235, 78, 235, 65)

    box(c, 5, 25, 460, 40, NEUTRAL, RULE)
    tx(c, 235, 53, 'Shared Bind rule (r is the predicted release time)', 9.5, align='center', bold=True)
    multi(c, 235, 40,
          ['If Y fits by the assigned reservation sizes, or r <= s: s + fresh-path overhead',
           'Otherwise: max(s + fresh-path overhead, r + release-to-Bind overhead)'],
          8.3, align='center', line_height=11)
    tx(c, 235, 10, 'Predictions are recorded only; they do not control scheduling.',
       8.3, align='center', color=SUB)
    c.save()


CONDITIONS = [
    ('natural_early', 'Natural early', 'contended (12)', 12),
    ('natural_boundary', 'Natural boundary', 'contended (36)', 36),
    ('spare', 'Spare capacity', '(24)', 24),
    ('delay', 'Signal delayed', 'contended (12)', 12),
    ('missing', 'Signal omitted', 'contended (12)', 12),
]
# Evidence keys retain their original names; only the displayed labels change.
MODELS = [('M3-new', 'Signal + Configured', ORANGE, 'circle'),
          ('M4-new', 'Calibrated Fixed', BLUE, 'square'),
          ('M3+M4', 'Signal + Calibrated', GREEN, 'diamond')]


def type7(values, p):
    vals = sorted(values)
    x = (len(vals)-1)*p
    i = math.floor(x)
    return vals[i] + (x-i)*(vals[min(i+1, len(vals)-1)]-vals[i])


def lighten(color, opacity):
    return Color(*(1 - (1-v)*opacity for v in color.rgb()))


def mark(c, x, y, r, kind, color, *, solid=True):
    c.setStrokeColor(color)
    c.setFillColor(color if solid else WHITE)
    c.setLineWidth(.72)
    if kind == 'circle':
        c.circle(x, y, r, stroke=1, fill=1)
    elif kind == 'square':
        c.rect(x-r*.83, y-r*.83, r*1.66, r*1.66, stroke=1, fill=1)
    else:
        poly(c, [(x-r, y), (x, y-r), (x+r, y), (x, y+r)], color if solid else WHITE, color, .72)


def distribution(out, csv_path):
    values = defaultdict(dict)
    trial_meta = {}
    with csv_path.open(newline='', encoding='utf-8') as src:
        for row in csv.DictReader(src):
            trial = row['trial_id']
            category, name = row['condition'], row['model']
            if row['scored'] != 'True' or row['bind_confirmed'] != 'True':
                raise ValueError(f'Unexpected missing scored trial: {trial}')
            if trial_meta.setdefault(trial, (category, row['y_uid'])) != (category, row['y_uid']):
                raise ValueError(f'Inconsistent trial identity: {trial}')
            dest = values[(category, name)]
            if trial in dest:
                raise ValueError(f'Duplicate prediction: {trial} ({name})')
            dest[trial] = float(row['absolute_error_ms'])
    if len(trial_meta) != 96:
        raise ValueError('Expected 96 complete trials')
    for category, _, _, n in CONDITIONS:
        sets = [set(values[category, key]) for key, _, _, _ in MODELS]
        if len(sets[0]) != n or not all(s == sets[0] for s in sets):
            raise ValueError(f'Incorrect or unmatched scored denominator: {category}')
    all_errors = [v for cell in values.values() for v in cell.values()]
    if min(all_errors) < .03 or max(all_errors) > 1000:
        raise ValueError('The plot scale must accommodate every observation')

    c = new(out, 'error-distribution', 285, 'Absolute Bind prediction error by evaluation condition')
    tx(c, 5, 271, 'Absolute Bind prediction error', 11.3, bold=True)
    tx(c, 465, 272, '96 trials, all scored', 8.1, align='right', color=SUB)
    for x, (_, label, color, kind) in zip((32, 148, 251), MODELS):
        mark(c, x, 250, 3, kind, color)
        tx(c, x+8, 247, label, 8.5)
    line(c, 359, 249, 359, 257, color=SUB, width=1.1)
    line(c, 356, 257, 362, 257, color=SUB, width=1.1)
    mark(c, 359, 249, 2.5, 'circle', SUB)
    tx(c, 368, 247, 'median to p95', 8.5)

    left, right, bottom, top = 54, 461, 49, 229
    ymin, ymax = math.log10(.03), 3
    def y(v):
        return bottom + (math.log10(v)-ymin)/(ymax-ymin)*(top-bottom)
    for value in (.03, .1, .3, 1, 3, 10, 30, 100, 300, 1000):
        yy = y(value)
        line(c, left, yy, right, yy, color=GRID, width=.62)
        tx(c, left-6, yy-2.7, f'{value:g}', 7.7, align='right', color=SUB)
    line(c, left, bottom, left, top, color=RULE)
    c.saveState()
    c.translate(13, (top+bottom)/2)
    c.rotate(90)
    tx(c, 0, 0, 'Absolute error (ms, log scale)', 9, align='center', bold=True)
    c.restoreState()

    cell_width = (right-left)/len(CONDITIONS)
    rng = random.Random(20261112)
    for i, (condition, line1, line2, _) in enumerate(CONDITIONS):
        cx = left+(i+.5)*cell_width
        if i:
            line(c, cx-cell_width*.5, bottom, cx-cell_width*.5, top, color=GRID, dash=[2,2])
        multi(c, cx, 34, (line1, line2), 8.1, align='center', line_height=11, bold=True)
        for j, (key, _, color, kind) in enumerate(MODELS):
            xx = cx+(j-1)*19.7
            data = [v for _,v in sorted(values[(condition,key)].items())]
            for v in data:
                mark(c, xx+rng.uniform(-5.2, 1.6), y(v), 1.30, kind, lighten(color, .45))
            y50, y95 = y(type7(data, .5)), y(type7(data, .95))
            marker = xx+4.5
            line(c, marker, y50, marker, y95, color=color, width=1.13)
            line(c, marker-3.2, y95, marker+3.2, y95, color=color, width=1.13)
            mark(c, marker, y50, 2.7, kind, color)
    tx(c, 54, 7, 'Faint marks: individual requests; horizontal jitter is for legibility only.', 7.6, color=SUB)
    c.save()


def boundary_gain(out, csv_path):
    """Render the same 36 points, assigned strata, and session means as Figure 3."""
    with csv_path.open(newline='', encoding='utf-8') as stream:
        rows = [(int(r["cleanup_s"]), int(r["session"]),
                 float(r["target_minus_cleanup_s"]), float(r["gain_ms"]))
                for r in csv.DictReader(stream)]
    if len(rows) != 36 or {(d, s, off) for d, s, off, _ in rows} != {
            (d, s, off) for d in (2, 5) for s in range(1, 7) for off in (-.5, 0, .5)}:
        raise ValueError("Expected 36 prespecified paired boundary observations")
    if not all(-32 <= gain <= 970 for *_, gain in rows):
        raise ValueError("The original y-axis range must contain all gains")
    c = new(out, 'boundary-gain', 193, 'Paired boundary forecast error gains')
    bottom, top = 39, 152
    bounds = ((50, 234), (277, 463))
    centers = lambda a, b, x, lo, hi: a + (x-lo)/(hi-lo)*(b-a)
    y = lambda v: centers(bottom, top, v, -32, 970)
    offset_x = lambda x: centers(*bounds[0], x, -.73, .73)
    session_x = lambda x: centers(*bounds[1], x, .45, 6.55)
    tx(c, mean(bounds[0]), 171, '(a) Assigned arrival offset', 9.2, bold=True, align='center')
    tx(c, mean(bounds[1]), 171, '(b) Evaluation session', 9.2, bold=True, align='center')
    c.saveState()
    c.translate(10, (top+bottom)/2)
    c.rotate(90)
    tx(c, 0, 0, 'Paired error gain (ms)', 9, bold=True, align='center')
    c.restoreState()
    for a, b in bounds:
        for tick in (0, 200, 400, 600, 800):
            yy = y(tick)
            line(c, a, yy, b, yy, color=GRID if tick else SUB, width=.6)
            tx(c, a-5, yy-2.5, str(tick), 7.2, color=SUB, align='right')
        line(c, a, bottom, b, bottom)
    for d, s, offset, value in rows:
        jitter = 1 if d == 5 else -1
        color, shape = (ORANGE, 'square') if d == 5 else (BLUE, 'circle')
        mark(c, offset_x(offset+.022*(s-3.5)+.014*jitter), y(value), 2.0, shape, color)
        mark(c, session_x(s+.13*offset+.043*jitter), y(value), 2.0, shape, color)
    for s in range(1, 7):
        x = session_x(s)
        yy = y(mean(v for _, ss, _, v in rows if ss == s))
        line(c, x-4, yy, x+4, yy, color=INK, width=1.25)
        tx(c, x, 26, str(s), 7.8, align='center')
    for x, label in ((-.5, '-0.5'), (0, '0'), (.5, '+0.5')):
        tx(c, offset_x(x), 26, label, 7.8, align='center')
    for x, label, shape, color in ((59, '2 s cleanup', 'circle', BLUE),
                                  (135, '5 s cleanup', 'square', ORANGE)):
        mark(c, x, 157, 2.0, shape, color)
        tx(c, x+8, 154, label, 7.5)
    line(c, 294, 157, 304, 157, color=INK, width=1.3)
    tx(c, 310, 154, 'Session mean', 7.5)
    tx(c, mean(bounds[0]), 9, 'Assigned arrival minus cleanup (s)', 8.2, align='center')
    tx(c, mean(bounds[1]), 9, 'Session (single host)', 8.2, align='center')
    c.save()


def main():
    arg = argparse.ArgumentParser(description=__doc__)
    arg.add_argument('--predictions', type=Path, required=True)
    arg.add_argument('--boundary-gains', type=Path, required=True)
    arg.add_argument('--out', type=Path, required=True)
    arg.add_argument('--font-dir', type=Path)
    args = arg.parse_args()
    register_fonts(args.font_dir)
    args.out.mkdir(parents=True)
    timeline(args.out)
    model(args.out)
    boundary_gain(args.out, args.boundary_gains)
    distribution(args.out, args.predictions)


if __name__ == '__main__':
    main()
