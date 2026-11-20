# Study design and HTTP contention

Table numbers are from the English manuscript. Counts in rows marked as
reanalysis refer to existing trials, not additional physical observations.

## Table 2: HTTP crossover assignments

Every block includes all eight combinations of exit mode, capacity condition
and arrival stratum. Offsets are relative to X's retirement intent.

| Stage | Early / late offset (s) | Blocks | Trials |
| --- | ---: | ---: | ---: |
| Calibration | +1.5 / +6 | 4 | 32 |
| Evaluation | +1.5 / +6 | 8 | 64 |
| Confirmation | +2.5 / +7 | 8 | 64 |

### HTTP contention outcomes reported in the Results text

Exit A is prompt normal exit; B has 5 s of cleanup. The interaction is
`(B − A) under contention − (B − A) with spare capacity`, within a block.
The A and B means below refer to early arrivals under contention.

| Batch | Early A: arrival-to-Bind (s) | Early B: arrival-to-Bind (s) | Early interaction (s) | Block-bootstrap interval (s) | Late interaction (ms) |
| --- | ---: | ---: | ---: | --- | ---: |
| Evaluation | 0.107 | 3.889 | 3.780 | 3.639–3.926 | −2.09 |
| Confirmation | 0.106 | 2.875 | 2.766 | 2.628–2.905 | −0.73 |

Each batch had eight early contended B trials. All eight logged insufficient
memory; no other trial did. The delayed-release model identified eight of eight
positives in each batch, whereas the immediate-release model identified none.
These are results from one Kind host, not estimates of production incidence.
Recomputed files: `kind/http/evaluation.json` and `kind/http/confirmation.json`.

## Table 4: Calibration and evaluation by campaign

| Campaign | Calibration | Evaluation | Comparison | Question |
| --- | --- | --- | --- | --- |
| HTTP crossover | 32 physical trials | 64 + 64 new | A/B, contended/spare, early/late | Does cleanup block another application under contention? |
| Fixed-delay boundary | Previously sealed fixed value | 80 (40 contended) | Configured duration vs. older calibrated fixed delay | Does the older calibration transfer? |
| Signal boundary | Prior constants; 10 preflight trials | Another 80 (40 contended) | Configured duration vs. original signal model | Does a handler event improve the historical forecast? |
| Fixed-delay update | 2 usable from the 10 preflight trials | Same signal-boundary 80 | Add a same-day fixed delay | Does updating the fixed value explain part of the gap? |
| Event forecast | New 24 | 56, four sessions | Signal + Configured vs. Calibrated Fixed | What is the value of signals available at the pre-create cutoff? |
| Fallback replay | Same sealed 24 | Same 56, post hoc | Use Calibrated Fixed rather than the configured duration when no signal is available | What motivates Signal + Calibrated? |
| Fallback test | New 24 | 96, six sessions | Signal + Calibrated vs. Signal + Configured (early) or Calibrated Fixed (boundary) | Does the prespecified comparison hold on new trials? |
| Retirement control | New 8 after an 8-run timing pilot | 48, six blocks | Forced wait vs. reclaim; fixed vs. signal | When do the action and the signal change outcomes? |

## Table 5: IBM replay input contract

| Status | Replay setting |
| --- | --- |
| Changed within a pair | Reservation release: immediate at retirement vs. the selected cache-derived A/B delay. |
| Held fixed within a pair | Replay window, request order and durations, 32-app settings, 8 or 16 GB cap, daily budget, preparation definition, scheduler and tie-breaking rules, frozen policy and common randomization contract. |
| Not an input | Kind release forecasts or actual future IBM requests in the policy lookahead. |

The contract controls policy settings, not the actions resulting from changes
to the simulated state.
