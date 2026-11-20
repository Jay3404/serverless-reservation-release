# Supplementary PPO D comparison

Recompute the six archived policy summaries from the normalized request and
instance ledgers. This does not train the five models or serialize their
weights.

```sh
python3 -B code/ppo/reproduce.py --evidence /path/to/evidence/ppo --out ppo.json
```

The script recounts completed and cold requests, separately sums initialization
and placement waits, and recomputes cost and tail from the instance ledger.
It checks equality of the first four request/assignment ledgers, compares the
D11/D33 TTL changes against the matching idle episodes, and recomputes the
Planner comparison for D55. The distinct checkpoint and weight digests are
archived measurements, not a claim that the checkpoints ship with this
artifact. The curator-only exporter reads the inventory-pinned source arrays
with `allow_pickle=False` and strips unnecessary identifiers.
