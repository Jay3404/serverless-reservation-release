# Figures

Figures 1–4 are emitted by [prediction reproduction](../prediction/README.md)
when `--figures` is supplied: the event and model diagrams, and the two
plots of Bind prediction results. Their inputs are the recomputed prediction
and boundary-gain CSVs. See [the result map](../../RESULTS.md).

For Figure 3 with the original manuscript layout, install Matplotlib and add
`--paper-boundary-figure` to the root command. Supply
`--korean-font /path/to/UnDotum.ttf` for the Korean variants of Figures 3 and 5.
The default Figure 3 renderer uses the same data with a different layout.

## Figure 5: Kind control comparisons

The figure combines paired measured latencies and a separate first-decision
replay. Generate `block_pairs.csv` with `verify_kind.py score` and
`first_decisions.csv` with `verify_kind.py replay`; see the
[Kind guide](../kind/README.md).

From the repository root, install the optional dependency in a virtual
environment and run:

```sh
python3 -m pip install reportlab==4.4.9
python3 -B code/figures/plot_figure5.py \
  --first checked-setting-replay/first_decisions.csv \
  --pairs checked-kind48/block_pairs.csv \
  --out generated-figures
```

The script validates trial IDs, blocks, observed first deletions, and latency
differences before writing `config-control-en.pdf`. To generate the Korean
version as well, add `--language both --korean-font /path/to/UnDotum.ttf`. The
Korean font is not distributed with this artifact.

Each latency point is a measured difference within the same block (signal
minus the named baseline). The setting-specific fixed comparator was replayed
for first decisions only; the figure does not assign it a measured response
time.
