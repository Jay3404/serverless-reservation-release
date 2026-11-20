# Supplementary PPO comparison

The English manuscript reports the following values in the text, not in a
numbered table. Both policies used the first 8-GB replay window with 25,319
arrivals and a reservation budget of 392,845.201 GB·s.

| Policy | Cold requests | Reservation (GB·s) | Budget-eligible |
| --- | ---: | ---: | --- |
| Planner | 622 | 388,558.307 | Yes |
| PPO D55 | 691 | 376,476.935 | Yes |

D55 was the only eligible PPO seed (1/5). It had 69 more cold requests than
Planner, but total wait was 0.85% smaller. This is a supplementary comparison,
not an overall PPO advantage.

| Ledger check reported in the manuscript | Result |
| --- | --- |
| Distinct checkpoint digests and distinct weight digests | 5 of each |
| Seeds with the same request assignment, cold, and waiting records | D11, D22, D33, D44 |
| D11/D33 TTL changes ending before 60 s | 319: 318 reuses, 1 capacity reclamation |
| D22 and D44 additional tail reservation relative to TTL60 | 2,160 GB·s each |
| D55 voluntary reclaim events | 471 |

The result is recomputed in `ppo.json` by the [PPO auditor](../code/ppo/README.md).
No PPO retraining is needed to recalculate the table.
