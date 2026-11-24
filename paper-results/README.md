# Manuscript result tables

The tables below reproduce the values **as displayed in the manuscript**.
They can be read on GitHub without downloading the evidence archive, installing
software, or starting Kubernetes. Units, comparison direction, and distinctions
between observations and replay are retained. Only manuscript-level values are
included; these pages are not request ledgers or simulator inputs.

Table numbers follow the English and Korean manuscript builds. A dash means
the result does not have its own numbered table in that language.

| Result | English | Korean | Read |
| --- | --- | --- | --- |
| HTTP crossover design and reported contention outcomes | 2 + Results text | 2 + Results text | [Study design and HTTP](study-design.md#table-2-http-crossover-assignments) |
| Calibration medians | 3 | — | [Prediction](prediction.md#table-3-calibration-medians) |
| Campaign design and replay contract | 4–5 | 3–4 | [Study design](study-design.md#table-4-calibration-and-evaluation-by-campaign) |
| Prespecified prediction comparisons | 6 | 5 | [Prediction](prediction.md#table-6-prespecified-prediction-comparisons) |
| Absolute prediction errors | 7 | 6 | [Prediction](prediction.md#table-7-absolute-prediction-errors) |
| Session and leave-one-out comparisons | 8 | 7 | [Prediction](prediction.md#table-8-session-and-leave-one-out-comparisons) |
| IBM return-delay ranges | 9 | Results text | [IBM replay](ibm.md#table-9-narrowed-cache-release-versus-immediate-release) |
| IBM budget example | 10 | 8 | [IBM replay](ibm.md#table-10-planner-budget-crossover) |
| IBM TTL5 oracle | 11 | 9 | [IBM replay](ibm.md#table-11-ttl5-fixed-signal-and-oracle) |
| Kind forced actions | 12 | 10 | [Kind control](kind-control.md#table-12-forced-action-and-fixed-versus-signal-comparisons) |
| Kind four-arm outcomes | 13 | 11 | [Kind control](kind-control.md#table-13-separate-kind-evaluation-by-arm) |
| Kind matched differences | 14 | 12 | [Kind control](kind-control.md#table-14-matched-differences-in-the-separate-kind-evaluation) |
| Within-setting Kind calibration | 15 | 13 | [Calibration](within-setting.md#table-15-within-setting-forced-reclaim-calibration) |
| Within-setting Kind outcomes and matched contrasts | 16 + Figure 6 | 14 + Figure 6 | [Evaluation](within-setting.md#table-16-within-setting-evaluation) |
| Calibration distributions | A.17 | A.15 | [Prediction appendix](prediction.md#table-a17-calibration-distributions) |
| Three reported boundary cases | B.18 | B.16 | [Prediction appendix](prediction.md#table-b18-largest-boundary-gains) |
| IBM cache-bracket replay, window 1 (42 cells) | C.19 | C.17 | [IBM appendix](ibm-appendix.md#table-c19-window-1-cache-bracket-replay) |
| IBM cache-bracket replay, window 2 (42 cells) | C.20 | C.18 | [IBM appendix](ibm-appendix.md#table-c20-window-2-cache-bracket-replay) |
| IBM TTL5 horizon sensitivity | C.21 | C.19 | [IBM appendix](ibm-appendix.md#table-c21-ttl5-horizon-sensitivity) |
| Supplementary PPO comparison | Appendix text | Appendix text | [PPO](ppo.md) |

The related-work comparison (Table 1) is not an experimental result and is
not reproduced here. To regenerate the numerical results rather than read
this reference copy, see the [result-to-command map](../RESULTS.md).
