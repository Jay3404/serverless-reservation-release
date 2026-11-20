# Prespecified scoring plan: four matched Kind baseline arms

Scope: this applies to the new evaluation in `KIND_PROTOCOL.md`, not the
historical forced-action 48 trials or calibration. The physical cluster releases
reservations in the same way for all arms. The controller-only instantaneous
forecast is not the IBM simulation's physical zero-return delay.

## Units, contrasts, and denominator

The denominator is all 48 assigned trials: six randomized blocks, each with a
fast-X and a slow-X trial for each of Kubernetes (no additional retirement),
instant forecast, fixed calibration, and signal with fixed fallback. Each trial
plans one Y and one Z HTTP request. Report attempted and complete trials, and
scorable counts per metric; retain unrun, incomplete and timeout rows without
imputation. Keep a missing measurement separate from observed zero. A metric
may be scored in an otherwise incomplete trial if it has all required evidence.
Report 36 planned paired contrasts: in each condition and block, signal versus
each of Kubernetes, instant and fixed. Never pair across blocks or conditions;
the six blocks are same-host repetitions, not independent hosts.

The main metric is observed Y ingress-to-Bind, measured in seconds, subtraction
sign **signal minus baseline** (negative = earlier). A Bind uses the API audit
`ResponseComplete` success for the same Y UID on the assigned node, joined to
the scheduler's success log. The predeclared 50 ms descriptive band for this
metric is not an equivalence test or a rule for dropping values; report exact
deltas, signs and band counts. Report condition/arm means, medians and ranges
with observed denominators and show signed differences in all paired blocks.
No significance, noninferiority, or generic platform improvement claim is
inferred from the band or these repetitions.

## Actions, actual capacity and service

Keep the initial and arrival+1 s choices separate. A proposal, route being
marked unavailable, and an acknowledged extra deletion are distinct. Count an
applied Z DELETE only with a controller receipt for the same Z UID carrying a
deletion timestamp; use the API submission timestamp to locate it within the
cost window and retain acknowledgment time. No Z mutation in the Kubernetes
arm means no additional route-disable or controller DELETE during that window,
not that teardown never deletes Z. Record Y create-call time and UID in every
arm; submitting Y is not gated on Z release or the DELETE result. Do not infer
from an observed lack of delay that small pre-create execution time is exactly
identical. The base audit policy preserves binding, not all DELETE requests:
negative mutation evidence relies on the guarded controller receipt and action
ledger plus consistent same-UID HTTP and scheduler evidence, rather than
purportedly complete audit coverage of every possible API call.

For the instant arm, zero forecast remaining time and X's forecast bytes may
coexist with scarce physical memory. Require affirmative actual X reservation
and insufficient actual free bytes, a wait proposal with no first Z deletion,
and same-Y memory no-fit followed by a recorded scheduler retry and verified
Bind for a representative affirmative witness. Y is not necessarily submitted
at the first decision; use the subsequent scheduler events, never an invented
instantaneous Pending flag. At recheck, preserve both observed API Pending and
controller input fields. Compare signal eligibility using matching X UID and
event, receipt and validation timestamps at each decision; later evidence
cannot serve as the original forecast input. Track recheck bypass reasons.

Attribute the sufficient reservation release to X or Z only when raw memory
no-fit and verified Bind exist, the runner has a complete capacity ledger, and
raw cache deletion/requeue log brackets, ordering, and released request bytes
identify it before Bind. Incomplete/overlapping bounds that prevent attribution
remain unconfirmed. Cache events bound scheduler accounting; they are not exact
physical memory-release measurements. Inactive Z teardown is not evidence of
a Z release causing Y's earlier Bind.

Report Y/Z ingress-to-client-response time only for identity-verified HTTP 200
on the correct user request, using the recorded serving UID and boot identity
where available. Distinguish within-window completion, observed completion
after the window, explicit timeout/HTTP failure, and unscored outcomes. Original
raw trial status may count window crossing as incomplete even when a verified
HTTP success was subsequently collected. Do not treat a late completion as
within-window, censor its observed latency to 8.5 s, or interpret 8.5 s as a
service SLO. Count replacement Pod creations separately from verified
replacements that actually served Z. Analyze timing and cost trade-offs for
both apps; a faster Y does not mean the Z request or aggregate system improved.

## Reservation cost and reproducibility

The common 8.5 s window starts at X retirement intent. Integrate only assigned
scheduler-requested capacity of X, original Z, Y, and replacement Z over that
window; do not count filler/system Pods, pending unassigned reservations, RSS,
or anything after the window. Use independently recomputed bounds from each
possible start and stop interval in the same clock. These are uncertainty
bounds from event observation, not statistical confidence limits. Where an
endpoint is censored, neither its integration window clip nor a midpoint is the
observed exact release. If rows differ in scope/basis or a bound cannot be
verified, leave paired cost missing. Subtract the baseline upper endpoint
from the signal lower endpoint, and the baseline lower from the signal upper.
Indicate whether this delta interval is strictly below or above zero or
contains zero; a zero-containing interval is inconclusive about ordering.

Score only the sealed 48-row grid and match raw trial metadata, terminal
receipt, hashes and Pod UID non-overlap with the separate calibration if
supplied. The new scripts pin scoring code/document before evaluation; the
scorer reuses only checksum-verified frozen utilities. Preserve raw files
unchanged, do not overwrite earlier scores, and write `trial_scores.csv`,
`block_pairs.csv`, `arm_results.csv`, `pair_results.csv` and
`scoring_manifest.json`. A run log or synthetic admission check is not a
physical outcome. If the physical gate fails or rows are unavailable, report
the available counts and evidence without assigning scores to unrun trials.
