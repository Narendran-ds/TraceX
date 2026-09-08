# Evaluation set

## What this measures

Every case in `cases/` is a **constructed scenario with known ground truth**. The
set answers one question honestly:

> Does the engine do what its own documentation says it does?

That is: fire on the shapes it defines, stay silent immediately below its
thresholds, and refuse to flag legitimate activity that looks identical to
laundering.

## What this does not measure

**Real-world detection accuracy.** No case here is a trace from a documented
laundering incident, so no number from this set may be presented as "we detect
N% of real laundering". That would be exactly the overclaim this project exists
to avoid.

`scripts/evaluate.py` prints the composition of the set on every run and states
this in its own output, so the distinction survives being copied into a slide by
someone in a hurry.

## Adding a real case

Write a file into `cases/` in the same shape, with `ground_truth_source` set to
the **public write-up URL** rather than the constructed marker. The harness picks
it up automatically and the composition line in the report changes on its own.

Good sources (master report §15.1):

- exchange-hack post-mortems where researchers later published the laundering path
- ransomware campaigns with published payment-address analysis
- the hacks and ransomware packs inside GraphSense TagPacks

Record, per case: the seed address, which patterns are genuinely present, and the
eventual exit point where it was published.

## The cases

| Case | Asserts |
|---|---|
| `fan-out-positive` | Six fresh recipients in the window fires fan-out |
| `fan-in-positive` | Convergence on an unattributed, new, low-volume collector fires fan-in |
| `peel-chain-positive` | Four hops forwarding a dominant remainder fires peel chain |
| `round-split-positive` | Four equal round parts fires round-number splitting |
| `timing-burst-positive` | Six transactions inside the tight window fires timing burst |
| `sanctioned-match-positive` | An OFAC address fires the attribution hit **and** degrades the trail |
| `fan-out-below-threshold` | One recipient short stays silent |
| `fan-out-outside-window` | Enough recipients spread over weeks stays silent |
| `legitimate-exchange-consolidation` | The control case: identical shape into a real exchange is **not** flagged |
| `even-splits-not-a-peel-chain` | 50/50 splits have no dominant remainder, so no peel chain |
| `clean-trace-to-exchange` | A clean trace scores zero and still attributes its exit |
| `timing-spread-not-a-burst` | Same transaction count, human pacing, stays silent |
| `single-transaction-not-a-burst` | One transaction with eight outputs is not a burst of eight |
| `bridge-dead-end` | The trail stops at a bridge and says so |
| `unattributed-exit-cluster` | An unmatched exit stays unnamed |
| `full-laundering-chain` | Several patterns compose on one trail |

The negative cases are the point. A detector that fires on everything scores
perfectly on positives alone.

## Running it

```bash
py -3.11 scripts/build_evaluation_set.py   # regenerate the cases
py -3.11 scripts/evaluate.py               # run and report
py -3.11 scripts/evaluate.py --json        # machine-readable
```

Results are written to `results/latest.md` and `results/latest.json`. The run
uses its own throwaway database (`evaluation.db`, gitignored) and never touches
the demo data.

## The manual baseline is not measured

The master report wants a manual-versus-automated time comparison, and it is the
safest strong metric available — self-generated, unfakeable, immediately
intuitive to a judge.

**Nobody has stopwatched a manual trace yet.** The harness reports the automated
time and prints `manual baseline: not measured` rather than inventing the other
half of the comparison. To close it: pick one case, trace it by hand through a
block explorer with a stopwatch running, and record the result here.
