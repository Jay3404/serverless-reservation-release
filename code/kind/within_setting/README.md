# Within-setting control evaluation

The root runner downloads the versioned public evidence and calls
`score_v2.py` to rescore the 12 calibration and 48 evaluation trials. It checks
the plans, frozen source/model hashes, Pod UID joins, decision-time signal
availability, scheduler release intervals, Bind audit and reservation bounds.
Figure 6 is redrawn from its trial and matched-pair CSVs by `plot.py`.

`PROTOCOL.md`, `run.py`, `run_kind.py`, `kind_base.py`, and
`run_kind_baselines.py` retain the frozen bytes referenced by the plan. The
physical runner is archived for inspection; the reproduction command does
not start a Kind cluster or contact the lab host. It does not run a physical
oracle or infer latency for actions that were never taken. The release also
retains the zero-trial terminal receipt for the pre-evaluation disk stop.

The release omits infrastructure and audit identity fields not used for
scoring. See `within-setting/NORMALIZATION.json` inside the archive and the
[projection script](../../../data/prepare_within_setting.py). Numerical
scored trial files are unchanged by the projection; only two private cluster
labels in the aggregate JSON are replaced by distinct public aliases.
