# Within-setting Kind control results

All three policies were physically executed in this later comparison. It is
separate from the historical 48-trial four-arm evaluation and its
setting-specific first-decision replay. Within each known cleanup setting,
the Pod specification stayed the same. The fixture injected 0 or 1.55 s
of additional pre-handler delay; no policy received the injected value.
Table numbers below follow the English manuscript.

## Table 15. Within-setting forced-reclaim calibration

The 12 separate calibration trials force additional retirement to measure
both release orders. Bounds describe scheduler reservation release relative
to the first decision and are not confidence intervals. The X/Z ordering
matched the planned case in every calibration trial.

| Configured cleanup (s) | Injected delay (s) | Lower X release bound (s) | Upper X release bound (s) | Observed order |
| ---: | ---: | ---: | ---: | --- |
| 2.9 | 0 | 0.108 | 0.195 | X before Z (3/3) |
| 2.9 | 1.55 | 1.673 | 1.732 | Z before X (3/3) |
| 3.1 | 0 | 0.351 | 0.453 | X before Z (3/3) |
| 3.1 | 1.55 | 1.870 | 1.905 | Z before X (3/3) |

The calibrated delay from retirement intent to X's release was 4.614 s
when pooled and 4.485/4.712 s for 2.9/3.1 s of cleanup, respectively.
For the signal policy, the corresponding calibrated residuals from
handler entry plus cleanup to release were 77.098 and 77.423 ms.
No evaluation outcomes were used for calibration.

## Table 16. Within-setting evaluation

All 48 assigned trials and 96 HTTP requests completed. The table gives
means among four trials per row. Deletions are first-decision plus later
actual Z deletions; Z recreation counts follow-up requests served by a
replacement. Bind and response times are measured from the corresponding
HTTP arrivals. Reservation bounds integrate assigned memory requests over
the common 8.5 s window from X's retirement intent, not physical memory use
or a confidence interval.

| Cleanup (s) | Injected delay (s) | Policy | Z deletions: first + later | Z recreated | Y Bind (s) | Y HTTP (s) | Z HTTP (s) | Reservation bounds (GB·s) |
| ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2.9 | 0 | Pooled Fixed | 4 + 0 | 4 | 0.165 | 2.274 | 2.055 | [2.28120, 2.28168] |
| 2.9 | 0 | Setting-specific Fixed | 0 + 0 | 0 | 0.165 | 2.331 | 0.087 | [2.28145, 2.28169] |
| 2.9 | 0 | Signal | 0 + 0 | 0 | 0.125 | 2.286 | 0.094 | [2.28148, 2.28169] |
| 2.9 | 1.55 | Pooled Fixed | 4 + 0 | 4 | 0.741 | 2.274 | 3.040 | [2.28117, 2.28168] |
| 2.9 | 1.55 | Setting-specific Fixed | 0 + 4 | 0 | 1.697 | 3.319 | 0.092 | [1.84740, 2.28169] |
| 2.9 | 1.55 | Signal | 4 + 0 | 4 | 0.738 | 2.298 | 3.035 | [2.28118, 2.28168] |
| 3.1 | 0 | Pooled Fixed | 4 + 0 | 4 | 0.343 | 2.290 | 2.032 | [2.28121, 2.28168] |
| 3.1 | 0 | Setting-specific Fixed | 4 + 0 | 4 | 0.351 | 2.310 | 2.066 | [2.28116, 2.28168] |
| 3.1 | 0 | Signal | 0 + 0 | 0 | 0.389 | 2.354 | 0.092 | [2.28141, 2.28169] |
| 3.1 | 1.55 | Pooled Fixed | 4 + 0 | 4 | 0.732 | 2.279 | 3.040 | [2.28119, 2.28168] |
| 3.1 | 1.55 | Setting-specific Fixed | 4 + 0 | 4 | 0.735 | 2.278 | 3.044 | [2.28117, 2.28168] |
| 3.1 | 1.55 | Signal | 4 + 0 | 4 | 0.734 | 2.349 | 3.085 | [2.28118, 2.28168] |

### Figure 6. Matched control outcomes

The outcome differences below are Signal minus Setting-specific Fixed,
matched by block and experimental condition. They compare separate physical
runs. Negative differences mean shorter time under Signal.

| Cleanup (s) | Injected delay (s) | Pairs | First deletions changed | Mean Δ Y Bind (s) | Mean Δ Y HTTP (s) | Mean Δ Z HTTP (s) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2.9 | 0 | 4 | 0 | −0.040 | −0.045 | +0.008 |
| 2.9 | 1.55 | 4 | 4 | −0.959 | −1.021 | +2.943 |
| 3.1 | 0 | 4 | 4 | +0.038 | +0.044 | −1.974 |
| 3.1 | 1.55 | 4 | 0 | −0.001 | +0.070 | +0.041 |
| All assigned cells | — | 16 | 8 | −0.241 | −0.238 | +0.254 |

With 2.9 s of cleanup and the longer disturbance, moving Z retirement to the
first decision advanced Y's Bind and delayed Z's response in all four
pairs. Setting-specific Fixed still deleted Z later in all four trials,
after the served Z request; zero recreations do not mean zero deletions.
With 3.1 s of cleanup and no injected delay, Signal avoided all four
retirements and recreations and shortened Z's response in all four pairs;
Y Bind was slower in three of the four pairs. The 16-pair aggregate weights
the four assigned cells equally and is not a production average.

The retrospective 1 s horizon classification agreed with the first
decision in 8/16 Pooled Fixed, 8/15 Setting-specific Fixed and 16/16 Signal
trials. One setting-specific trial lacked a complete release bracket. This
audit is not a physical oracle arm and does not provide oracle latency or
cost. Mean paired reservation differences included zero in every
condition. These results demonstrate that the signal can change the timing
of actual retirements under this controlled perturbation, not that it
uniformly improves service or reduces reservation cost.
