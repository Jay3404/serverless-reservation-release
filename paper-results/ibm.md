# IBM replay results

Published aggregates from the English manuscript. Reservation cost is in GB·s;
other placement wait excludes initialization. These are trace-replay results,
not measured Kubernetes service delays.

## Table 9: Narrowed-cache release versus immediate release

Each range covers two replay windows, three fixed policies, and three release
endpoints. A is prompt normal exit and B is 5-second cleanup. Wait and cost are
increases relative to the matching immediate-release row; the last column
counts contrasts meeting the study's substantial-wait criterion.

| Capacity / exit | Other wait increase (ms/arrival) | Reservation cost increase (GB·s) | Substantial wait increase |
| :--- | ---: | ---: | ---: |
| 8 GB / A | 0.133 to 0.749 | 76.79 to 168.58 | 0/18 |
| 8 GB / B | 24.933 to 113.263 | 4519.70 to 9287.16 | 18/18 |
| 16 GB / A | 0 to 0.019 | 81.21 to 179.38 | 0/18 |
| 16 GB / B | 0 to 1.027 | 4479.43 to 9410.05 | 0/18 |

## Table 10: Planner budget crossover

The first replay window and 8 GB capacity. The budget is **392,845.201 GB·s**.
Planner was the only eligible immediate-release operating point; it does not
remain eligible at the B midpoint.

| Scenario | Cost (GB·s) | Over budget (GB·s) | Other wait (ms/arrival) | Eligible |
| :--- | ---: | ---: | ---: | :--- |
| Immediate release | 391,878.398 | 0 | 0.069 | Yes |
| B midpoint | 396,669.253 | 3,824.052 | 25.013 | No |

## Table 11: TTL5 fixed, signal, and oracle

8 GB capacity, one-second decision horizon, and TRAIN-selected TTL5. Each
policy entry is **other wait (ms/arrival) / reservation cost (GB·s)**.
Day 1 has 25,319 arrivals and day 2 has 24,800. Completion, eligibility,
and SLO misses are common to all three policies within a row. The budget is
392,845.201 GB·s. The oracle uses the simulated release time of instances
already retiring at the decision.

| Cleanup (s) | Day | Fixed (wait / cost) | Signal (wait / cost) | Oracle (wait / cost) | Complete / eligible | SLO misses |
| ---: | ---: | ---: | ---: | ---: | :--- | ---: |
| 2 | 1 | 2.398975 / 262,270.035 | 2.402703 / 262,273.027 | 2.402703 / 262,276.702 | 100% / Yes | 194 |
| 2 | 2 | 10.765101 / 258,998.885 | 10.773444 / 259,001.672 | 10.773444 / 259,002.614 | 100% / Yes | 210 |
| 5 | 1 | 36.089280 / 331,169.102 | 36.089280 / 331,180.636 | 36.089280 / 331,180.636 | 100% / Yes | 196 |
| 5 | 2 | 47.507989 / 325,926.702 | 47.507989 / 325,950.307 | 47.507989 / 325,950.307 | 100% / Yes | 231 |

See [the appendix tables](ibm-appendix.md) for all 84 published narrowed-cache
rows and the TTL5 horizon sensitivity. See [RESULTS.md](../RESULTS.md) for the
commands that recompute these results from the separate evidence archive.
