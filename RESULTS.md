# Result-by-result reproduction

To inspect the figures' context and manuscript values without running code,
start with the [paper result tables](paper-results/README.md). Those Markdown
tables report the manuscript's displayed precision, not newly generated
outputs. The sections below describe how to recompute them.

Run the full command in the [root README](README.md). The paths in the last
column are relative to the output directory. All observed durations are from
the recorded trials, not from a new cluster or from synthetic measurements.

| Paper result | Input and recomputation | Output to inspect |
| --- | --- | --- |
| HTTP A/B contention and independent confirmation | Refit on the separate 32-trial calibration; score the two held-out 64-trial ledgers and recompute the paired contention contrasts. | `kind/http/` |
| RQ1: placement forecasts, both older 80-trial campaigns, separate 56- and 96-trial experiments | Fit from calibration; recompute the predictions, matched errors, endpoint-definition check, delay/loss groups, and calibration-only fixed-delay sensitivity. | `prediction/prediction_report.json`, request and paired-gain CSVs |
| Figures 1–4 | Generate event/model diagrams; regenerate boundary gains and error distributions from prediction CSVs. Rendering is not a new measurement. | `prediction/figures/` |
| RQ2: effect of charging delayed release, budget and rankings | Recount the separate 210 replay cells from request, instance and blocked-interval ledgers; recompute 174 paired contrasts and 70 ranking scenarios. This is ledger recomputation, not a rerun with the original RQ2 policies. | `ibm-release/` |
| RQ3: original TTL60 and finite TTL frontier | With `--full-ibm`, rerun the 58 TTL60 cells, TRAIN selection, selected DEV replay and the 144-cell frontier, then compare with the frozen ledgers and summaries. | `ttl60/`, `rebudget/`, `frontier/`, and independent audit directories |
| RQ3: TTL5, exact-return oracle, sensitivity and decision mechanisms | Reassemble the input from the frozen request windows and calibration; independently recompute the operating rows and oracle wait-event comparison. Add `--rerun-operating` to run all 80 operating and four TTL60 oracle cells again. Recount the 42/28 state differences, 23 action-switch comparisons and 32 reference cells from archived ledgers. | `ibm-operating/`, `decision-audit.json` |
| RQ3: Kind forced actions, scheduler/forecast baselines and setting-aware comparator | Recompute both 48-trial campaigns, the forced-wait check and the configuration-aware first decisions. Configuration replay assigns no latency to the unrun policy. | `kind/forced/`, `kind/baseline/`, `kind/replay/` |
| RQ3: within-setting physical evaluation | Recheck the separate 12-trial calibration and 48-trial evaluation; reconstruct UID-linked first decisions, release intervals, Bind and paired reservation outcomes. | `kind-within-setting/RESULTS.json`, `trials.csv`, `pairs.csv`, `summary.csv`, `contrasts.csv` |
| Figure 5 | Generate the updated layout from the paired measurements and first-decision replay above. | `figures/config-control-en.pdf` |
| Figure 6 | Regenerate actual first-Z-deletion counts and block-paired latency differences from the within-setting tables. | `figures/within-setting-en.pdf` |
| Supplementary PPO D comparison | Recount requests, waits and reservation tails across five seeds and Planner; compare recorded TTL actions and weight hashes. No training or checkpoint loading. | `ppo.json` |

`run-report.json` records which commands completed. With the default
options, archived-ledger checks are not evidence of a new simulation.
Replica runtime and PDF bytes may differ with hardware, fonts or rendering
libraries. Use the numerical results and ledger checks for comparisons.
