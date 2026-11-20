# Kind control results

Tables 12–14 use the English manuscript numbering. The forced-action study
in Table 12 and the four-arm controller evaluation in Tables 13–14 are separate
48-trial experiments. These are published summaries, not trial-level records.
See the [Kind reproduction guide](../code/kind/README.md) and [result map](../RESULTS.md).

## Table 12. Forced-action and fixed-versus-signal comparisons

Differences are left minus right for Y HTTP ingress to Bind (ms). Each row
contains six matched blocks. The last column counts blocks where the right
arm was earlier by more than 50 ms, within ±50 ms, or later by more than
50 ms, respectively. The range is the observed minimum and maximum difference.

| X condition | Contrast (left − right) | Mean (ms) | Range (ms) | Right earlier / within band / right later |
| --- | --- | ---: | ---: | ---: |
| Fast | Wait − reclaim | −7.7 | [−82.5, 63.8] | 1 / 4 / 1 |
| Slow | Wait − reclaim | +1225.3 | [1187.7, 1256.7] | 6 / 0 / 0 |
| Fast | Fixed − signal | −23.9 | [−68.6, 26.1] | 0 / 5 / 1 |
| Slow | Fixed − signal | +3.0 | [−8.2, 11.2] | 0 / 6 / 0 |

## Table 13. Separate Kind evaluation by arm

Each row covers six trials and 12 planned HTTP requests (six each for Y and
Z). The Bind and response times are means from HTTP ingress. Completion is
counted within the common 8.5 s window. Each reservation interval is the mean
of the observed bounds on assigned reservation integrated over that same
window; it is not a statistical confidence interval.

| Condition | Arm | Y Bind (s) | Y response (s) | Z response (s) | HTTP complete | Z DELETE / recreated | Reservation bounds (GB·s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Fast X | Kubernetes | 0.345 | 2.385 | 0.092 | 12/12 | 0 / 0 | [2.281455, 2.281693] |
| Fast X | Instant Forecast | 0.378 | 2.400 | 0.093 | 12/12 | 0 / 0 | [2.281464, 2.281693] |
| Fast X | Calibrated Fixed | 0.335 | 2.399 | 2.133 | 12/12 | 6 / 6 | [2.281192, 2.281663] |
| Fast X | Signal with fixed fallback | 0.345 | 2.428 | 0.092 | 12/12 | 0 / 0 | [2.281457, 2.281691] |
| Slow X | Kubernetes | 1.936 | 3.401 | 0.088 | 12/12 | 0 / 0 | [2.281440, 2.281693] |
| Slow X | Instant Forecast | 1.978 | 3.416 | 0.092 | 12/12 | 0 / 0 | [2.281437, 2.281691] |
| Slow X | Calibrated Fixed | 0.734 | 2.448 | 3.160 | 12/12 | 6 / 6 | [2.281183, 2.281679] |
| Slow X | Signal with fixed fallback | 0.751 | 2.413 | 3.133 | 12/12 | 6 / 6 | [2.281175, 2.281682] |

Z DELETE counts verified calls. Z recreated counts follow-up requests served
by a replacement.

## Table 14. Matched differences in the separate Kind evaluation

Each value is signal minus comparator, averaged over six matched blocks.
Negative time differences mean the signal arm was faster. Block signs use
the unrounded Y Bind differences and appear in block order. The band
counts blocks below −50 ms, within ±50 ms, and above +50 ms, respectively.
Reservation differences are the mean lower and upper bounds, not confidence
intervals.

| Condition | Comparator | Δ Y Bind (ms) | Bind signs (blocks 1–6) | Bind band counts | Δ Y response (s) | Δ Z response (s) | Δ reservation bounds (GB·s) |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: |
| Fast X | Kubernetes | +0.487 | `+ − − + + +` | 0 / 6 / 0 | +0.0428 | −0.0001 | [−0.000236, +0.000236] |
| Fast X | Instant Forecast | −32.630 | `− − + − − +` | 1 / 4 / 1 | +0.0280 | −0.0012 | [−0.000236, +0.000227] |
| Fast X | Calibrated Fixed | +10.376 | `+ + − − + −` | 0 / 5 / 1 | +0.0285 | −2.0412 | [−0.000206, +0.000500] |
| Slow X | Kubernetes | −1184.880 | `− − − − − −` | 6 / 0 / 0 | −0.9881 | +3.0457 | [−0.000518, +0.000242] |
| Slow X | Instant Forecast | −1227.243 | `− − − − − −` | 6 / 0 / 0 | −1.0033 | +3.0410 | [−0.000516, +0.000246] |
| Slow X | Calibrated Fixed | +16.777 | `+ + + − − +` | 0 / 5 / 1 | −0.0350 | −0.0268 | [−0.000504, +0.000499] |
