# Evaluation results

Generated 2026-09-08T11:30:36+00:00 by `scripts/evaluate.py`.
Every figure below was computed from the run that produced this file.

## What this set measures

- **16 constructed scenario(s)**
- **0 documented real-world case(s)**

> Every case in this set is constructed. These numbers show that the
> engine behaves as its own documentation specifies — it fires on the
> shapes it defines, stays silent below its thresholds, and declines to
> flag legitimate activity that looks identical.
>
> **They are not real-world detection accuracy.** Presenting them as
> such would be the exact overclaim this project refuses to make. To
> measure that, add traces from publicly documented laundering cases to
> `data/evaluation/cases/` with `ground_truth_source` set to the public
> write-up, and re-run this script.

## Measured results

| Metric | Value |
|---|---|
| Cases run | 16 |
| Cases correct on every assertion | 16 of 16 |
| Patterns correctly flagged | 13 |
| Patterns missed | 0 |
| False positives | 0 |
| Correctly not flagged | 64 |
| Precision | 100.0% |
| Recall | 100.0% |
| Correct exit in top 3 | 5 of 5 |
| Trail-degradation calls correct | 16 of 16 |
| Median time per case | 0.0073 s |
| Slowest case | 0.009 s |
| Addresses processed across the set | 114 |
| Transactions processed across the set | 72 |
| Manual tracing baseline | not measured |

## Per-case

| Case | Result | Detected | Missed | False positives | Time |
|---|---|---|---|---|---|
| `bridge-dead-end` | pass | — | — | — | 0.009 s |
| `clean-trace-to-exchange` | pass | — | — | — | 0.009 s |
| `even-splits-not-a-peel-chain` | pass | — | — | — | 0.009 s |
| `fan-in-positive` | pass | fan_in, fan_out | — | — | 0.008 s |
| `fan-out-below-threshold` | pass | — | — | — | 0.006 s |
| `fan-out-outside-window` | pass | — | — | — | 0.007 s |
| `fan-out-positive` | pass | fan_out | — | — | 0.007 s |
| `full-laundering-chain` | pass | cluster_context, fan_in, fan_out, peel_chain, round_split, timing_burst | — | — | 0.009 s |
| `legitimate-exchange-consolidation` | pass | — | — | — | 0.007 s |
| `peel-chain-positive` | pass | peel_chain | — | — | 0.006 s |
| `round-split-positive` | pass | round_split | — | — | 0.007 s |
| `sanctioned-match-positive` | pass | sanctioned_match | — | — | 0.007 s |
| `single-transaction-not-a-burst` | pass | fan_out | — | — | 0.008 s |
| `timing-burst-positive` | pass | cluster_context, fan_out, timing_burst | — | — | 0.008 s |
| `timing-spread-not-a-burst` | pass | — | — | — | 0.007 s |
| `unattributed-exit-cluster` | pass | — | — | — | 0.007 s |

## Manual baseline

Not measured. Stopwatch a manual trace of the same case by hand and record it here before any time comparison is presented.
