# Bind prediction results

Table numbers refer to the English manuscript. These are the reported numbers, with the manuscript's rounding. They do not include request ledgers or cluster identifiers. To recalculate the numbers from the separate evidence archive, see [the result map](../RESULTS.md) and [the prediction guide](../code/prediction/README.md).

## Table 3. Calibration medians

The terms are in ms, with **six observations per term and cleanup duration** from the appropriate capacity condition. `R_d`: fixed delay from retirement intent to reservation release; `rho_d`: residual from handler entry plus cleanup to release; `u_d`: fresh-placement overhead from Y pre-create cutoff to Bind; `v_d`: retry overhead from release to Bind.

| Evaluation campaign | Configured cleanup (s) | R_d (ms) | rho_d (ms) | u_d (ms) | v_d (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 56 trials | 2 | 2298.759 | 65.738 | 47.032 | 1.657 |
| 56 trials | 5 | 5308.852 | 65.949 | 46.915 | 1.827 |
| 96 trials | 2 | 2308.339 | 66.605 | 47.016 | 1.801 |
| 96 trials | 5 | 5263.360 | 68.022 | 47.258 | 1.747 |

## Table 6. Prespecified prediction comparisons

The boundary comparison tests the signal against **Calibrated Fixed**; the early comparison tests the fallback against **Signal + Configured**. Gain is the reference's absolute error minus Signal + Calibrated's absolute error on the same request, before rounding. These are prediction errors, not reductions in observed placement latency.

| Assigned condition | Signal available | Reference MAE (ms) | Signal + Calibrated MAE (ms) | Mean paired gain (ms) | Wins / ties |
| --- | ---: | ---: | ---: | ---: | ---: |
| Natural boundary (36 trials) | 36/36 | 109.285 | 3.114 | 106.171 | 25 / 11 |
| Natural early (12 trials) | 0/12 | 335.374 | 83.778 | 251.595 | 12 / 0 |

## Table 7. Absolute prediction errors

Values are **MAE / median / sample p95**, in ms. Natural competition is the subtotal of the early and boundary rows, not another set of trials.

| Assigned condition | n | Signal + Configured | Calibrated Fixed | Signal + Calibrated |
| --- | ---: | ---: | ---: | ---: |
| Natural early | 12 | 335.374 / 349.399 / 475.190 | 83.778 / 78.437 / 189.912 | 83.778 / 78.437 / 189.912 |
| Natural boundary | 36 | 3.114 / 2.677 / 8.103 | 109.285 / 34.261 / 670.688 | 3.114 / 2.677 / 8.103 |
| Natural competition (subtotal) | 48 | 86.179 / 3.555 / 389.022 | 102.908 / 48.585 / 480.568 | 23.280 / 3.351 / 108.265 |
| Spare capacity | 24 | 1.111 / 0.789 / 3.198 | 1.111 / 0.789 / 3.198 | 1.111 / 0.789 / 3.198 |
| Delayed signal | 12 | 291.429 / 300.991 / 394.168 | 58.196 / 46.416 / 158.375 | 58.196 / 46.416 / 158.375 |
| Omitted signal | 12 | 379.326 / 392.546 / 584.694 | 145.991 / 110.809 / 309.263 | 145.991 / 110.809 / 309.263 |

All 96 assigned Bind endpoints were scorable. The paper's p95 values describe the sample of absolute **prediction errors**, not p95 service latency.

## Table 8. Session and leave-one-out comparisons

Mean paired gain (ms) of Signal + Calibrated against **Signal + Configured** for early trials and **Calibrated Fixed** for boundary trials. Each session contains two early and six boundary trials. Leave-one-out averages exclude the listed session.

| Session | Within session: early (ms) | Within session: boundary (ms) | Leave one session out: early (ms) | Leave one session out: boundary (ms) |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 285.552 | 59.626 | 244.804 | 115.480 |
| 2 | 238.448 | 22.386 | 254.225 | 122.928 |
| 3 | 244.356 | 201.513 | 253.043 | 87.103 |
| 4 | 169.517 | 49.165 | 268.011 | 117.572 |
| 5 | 285.850 | 287.428 | 244.745 | 69.920 |
| 6 | 285.850 | 16.907 | 244.745 | 124.024 |

### Earlier validation and model variants

The separate 56-trial validation gave boundary MAEs of 63.898 ms for Calibrated Fixed and 3.529 ms for Signal + Configured on 24 requests. On eight early requests without an available signal, the configured-duration fallback gave 346.131 ms MAE versus 57.754 ms for Calibrated Fixed. Retrospective use of Signal + Calibrated on those same trials was exploratory.

Each earlier 80-trial campaign included 40 scored contended requests at the 5-s cleanup setting. In the fixed-delay campaign, MAE was 198.03 ms for the older calibrated fixed delay versus 178.53 ms using the configured cleanup duration alone. In the separate signal campaign, MAE was 92.55 ms for the configured duration versus 8.79 ms for the original signal model. A retrospective comparison in the second campaign gave 165.42 ms for the older fixed delay versus 42.69 ms for a same-day fixed delay fitted on two usable contended observations from separate preflight trials. These historical models used a common overhead rather than the later two-path rule.

## Table A.17. Calibration distributions

The observed ranges (ms) are minimum to maximum, **not confidence intervals**. Term definitions match Table 3.

| Evaluation campaign | Configured cleanup (s) | Term | n | Median (ms) | Observed range (ms) |
| --- | ---: | --- | ---: | ---: | --- |
| 56 trials | 2 | R_d | 6 | 2298.759 | 2167.940–3111.542 |
| 56 trials | 2 | rho_d | 6 | 65.738 | 62.326–75.017 |
| 56 trials | 2 | u_d | 6 | 47.032 | 45.355–50.000 |
| 56 trials | 2 | v_d | 6 | 1.657 | 1.588–1.914 |
| 56 trials | 5 | R_d | 6 | 5308.852 | 5094.699–6114.648 |
| 56 trials | 5 | rho_d | 6 | 65.949 | 62.178–71.045 |
| 56 trials | 5 | u_d | 6 | 46.915 | 44.900–54.958 |
| 56 trials | 5 | v_d | 6 | 1.827 | 1.661–1.957 |
| 96 trials | 2 | R_d | 6 | 2308.339 | 2108.728–2593.283 |
| 96 trials | 2 | rho_d | 6 | 66.605 | 59.829–140.128 |
| 96 trials | 2 | u_d | 6 | 47.016 | 44.628–50.470 |
| 96 trials | 2 | v_d | 6 | 1.801 | 1.696–1.854 |
| 96 trials | 5 | R_d | 6 | 5263.360 | 5101.851–5431.153 |
| 96 trials | 5 | rho_d | 6 | 68.022 | 62.204–73.703 |
| 96 trials | 5 | u_d | 6 | 47.258 | 45.841–47.905 |
| 96 trials | 5 | v_d | 6 | 1.747 | 1.708–1.896 |

### Post-hoc checks

- The full set of fixed delays that minimize calibration Bind MAE gives 108.515–113.267 ms MAE on the 36 boundary trials, versus 3.114 ms for Signal + Calibrated. The evaluation was not used to select a constant.
- Rescoring the same forecasts against the successful Bind API audit endpoint gave 109.273 versus 3.110 ms on boundary trials (106.163 ms paired gain) and 335.136 versus 83.700 ms on early trials (251.437 ms gain). This is a different recorded endpoint, not independent timing validation.

## Table B.18. Largest boundary gains

Only the three pseudonymous case summaries printed in the paper are shown. All event times are ms after retirement intent. `h` is handler entry, `s` the pre-create cutoff, and `b` the observed Bind. The cache observations bracket reservation release; they do not measure physical memory return.

| Paper trial label | h (ms) | s (ms) | Memory failure (ms) | Observed cache bracket (ms) | b (ms) | Paired error gain (ms) |
| --- | ---: | ---: | ---: | --- | ---: | ---: |
| S3-015 | 1103.955 | 5055.044 | 5101.783 | 6164.175–6164.219 | 6165.860 | 892.891 |
| S5-011 | 1146.165 | 2051.543 | 2097.612 | 3204.179–3204.219 | 3205.748 | 886.785 |
| S5-015 | 1132.025 | 5555.468 | 5603.154 | 6196.461–6196.503 | 6198.441 | 592.363 |

The three cases account for 62.1% of the summed boundary gain. Excluding them after inspecting the data leaves a 43.943 ms mean gain on 33 trials; the prespecified result keeps all 36.
