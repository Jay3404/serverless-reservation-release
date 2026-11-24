# IBM replay appendix tables

Published aggregates. A is prompt normal exit, B is 5-second cleanup, and
L/M/U denote lower/midpoint/upper release scenarios. The scenario `0` is
immediate release. Other placement wait excludes initialization and is
expressed per arrival. Reservation costs include modeled resource
holding times; eligibility uses the study budget of 392,845.201 GB·s.

## Table C.19: Window 1 cache-bracket replay

### 8 GB

| Release | Policy | Other wait (ms/arrival) | Cost (GB·s) | Eligible |
| :--- | :--- | ---: | ---: | :--- |
| 0 | TTL60 | 0.069 | 414,096.743 | No |
| 0 | Planner | 0.069 | 391,878.398 | Yes |
| 0 | Rule | 0.508 | 502,371.037 | No |
| AL | TTL60 | 0.262 | 414,173.537 | No |
| AL | Planner | 0.201 | 392,042.380 | Yes |
| AL | Rule | 0.996 | 502,493.934 | No |
| AM | TTL60 | 0.267 | 414,175.221 | No |
| AM | Planner | 0.205 | 392,044.680 | Yes |
| AM | Rule | 1.008 | 502,496.621 | No |
| AU | TTL60 | 0.272 | 414,176.904 | No |
| AU | Planner | 0.209 | 392,046.980 | Yes |
| AU | Rule | 1.019 | 502,499.309 | No |
| BL | TTL60 | 28.921 | 418,616.444 | No |
| BL | Planner | 25.002 | 396,667.168 | No |
| BL | Rule | 68.252 | 510,865.930 | No |
| BM | TTL60 | 28.935 | 418,618.189 | No |
| BM | Planner | 25.013 | 396,669.253 | No |
| BM | Rule | 68.285 | 510,868.527 | No |
| BU | TTL60 | 28.949 | 418,619.935 | No |
| BU | Planner | 25.024 | 396,671.339 | No |
| BU | Rule | 68.319 | 510,871.123 | No |

### 16 GB

| Release | Policy | Other wait (ms/arrival) | Cost (GB·s) | Eligible |
| :--- | :--- | ---: | ---: | :--- |
| 0 | TTL60 | 0.000 | 417,450.763 | No |
| 0 | Planner | 0.000 | 394,887.900 | No |
| 0 | Rule | 0.000 | 545,885.082 | No |
| AL | TTL60 | 0.000 | 417,531.969 | No |
| AL | Planner | 0.000 | 395,004.334 | No |
| AL | Rule | 0.014 | 546,054.200 | No |
| AM | TTL60 | 0.000 | 417,533.748 | No |
| AM | Planner | 0.000 | 395,006.880 | No |
| AM | Rule | 0.014 | 546,057.887 | No |
| AU | TTL60 | 0.000 | 417,535.526 | No |
| AU | Planner | 0.000 | 395,009.426 | No |
| AU | Rule | 0.015 | 546,061.387 | No |
| BL | TTL60 | 0.000 | 421,930.192 | No |
| BL | Planner | 0.000 | 399,395.869 | No |
| BL | Rule | 0.927 | 555,143.856 | No |
| BM | TTL60 | 0.000 | 421,931.983 | No |
| BM | Planner | 0.000 | 399,398.209 | No |
| BM | Rule | 0.928 | 555,147.556 | No |
| BU | TTL60 | 0.000 | 421,933.774 | No |
| BU | Planner | 0.000 | 399,400.550 | No |
| BU | Rule | 0.929 | 555,151.256 | No |

## Table C.20: Window 2 cache-bracket replay

### 8 GB

| Release | Policy | Other wait (ms/arrival) | Cost (GB·s) | Eligible |
| :--- | :--- | ---: | ---: | :--- |
| 0 | TTL60 | 13.776 | 419,391.774 | No |
| 0 | Planner | 12.637 | 394,200.592 | No |
| 0 | Rule | 13.776 | 503,919.561 | No |
| AL | TTL60 | 14.203 | 419,470.087 | No |
| AL | Planner | 12.987 | 394,292.656 | No |
| AL | Rule | 14.488 | 504,023.121 | No |
| AM | TTL60 | 14.213 | 419,471.803 | No |
| AM | Planner | 12.995 | 394,295.091 | No |
| AM | Rule | 14.506 | 504,026.078 | No |
| AU | TTL60 | 14.223 | 419,473.518 | No |
| AU | Planner | 13.002 | 394,297.526 | No |
| AU | Rule | 14.524 | 504,029.034 | No |
| BL | TTL60 | 73.666 | 423,929.336 | No |
| BL | Planner | 70.374 | 399,819.114 | No |
| BL | Rule | 126.940 | 513,201.313 | No |
| BM | TTL60 | 73.687 | 423,931.099 | No |
| BM | Planner | 70.397 | 399,821.400 | No |
| BM | Rule | 126.989 | 513,204.016 | No |
| BU | TTL60 | 73.708 | 423,932.861 | No |
| BU | Planner | 70.419 | 399,823.686 | No |
| BU | Rule | 127.039 | 513,206.720 | No |

### 16 GB

| Release | Policy | Other wait (ms/arrival) | Cost (GB·s) | Eligible |
| :--- | :--- | ---: | ---: | :--- |
| 0 | TTL60 | 0.000 | 424,195.686 | No |
| 0 | Planner | 0.000 | 398,704.847 | No |
| 0 | Rule | 0.000 | 551,277.183 | No |
| AL | TTL60 | 0.000 | 424,281.547 | No |
| AL | Planner | 0.000 | 398,861.179 | No |
| AL | Rule | 0.019 | 551,449.055 | No |
| AM | TTL60 | 0.000 | 424,283.426 | No |
| AM | Planner | 0.000 | 398,864.006 | No |
| AM | Rule | 0.019 | 551,452.811 | No |
| AU | TTL60 | 0.000 | 424,285.304 | No |
| AU | Planner | 0.000 | 398,866.833 | No |
| AU | Rule | 0.019 | 551,456.567 | No |
| BL | TTL60 | 0.000 | 428,928.368 | No |
| BL | Planner | 0.000 | 403,345.944 | No |
| BL | Rule | 1.026 | 560,679.653 | No |
| BM | TTL60 | 0.000 | 428,930.260 | No |
| BM | Planner | 0.000 | 403,348.483 | No |
| BM | Rule | 1.026 | 560,683.443 | No |
| BU | TTL60 | 0.000 | 428,932.152 | No |
| BU | Planner | 0.000 | 403,351.021 | No |
| BU | Rule | 1.027 | 560,687.233 | No |

## Table C.21: TTL5 horizon sensitivity

8 GB capacity, 2-second cleanup. Each signed entry is the named policy
minus the fixed estimate within the same validation window. All cells
completed and met the common budget. The queue-check interval was one
second for all decision horizons.

| Decision horizon (s) | Day | Signal Δ wait (ms/arrival) | Signal Δ cost (GB·s) | Oracle Δ wait (ms/arrival) | Oracle Δ cost (GB·s) |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5 | 1 | 0 | -5.154 | 0 | -2.704 |
| 0.5 | 2 | -0.025007 | +4.851 | -0.025007 | +4.851 |
| 1.0 | 1 | +0.003727 | +2.992 | +0.003727 | +6.667 |
| 1.0 | 2 | +0.008342 | +2.787 | +0.008342 | +3.729 |
| 2.0 | 1 | 0 | +0.568 | 0 | +3.018 |
| 2.0 | 2 | +0.008342 | +12.399 | +0.008342 | +13.341 |

See [IBM main tables](ibm.md) and the [reproduction map](../RESULTS.md).
