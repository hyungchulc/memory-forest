# Provenance and promotion

Memory Forest treats provenance as part of the memory, not as optional metadata added later.

## Minimum provenance record

A useful entry records the smallest set that lets a future reader evaluate it.

```yaml
source: synthetic field log demo-log-01
source_timestamp: 2042-04-12T09:10:00Z
captured_at: 2042-04-12T09:12:00Z
scope: calibration attempt 2
observed_fact: reference drift remained below the demo threshold
derived_conclusion: the instrument was stable enough for the fictional trial
uncertainty: only one synthetic reference cycle was observed
promotion_from: 05 daily/2042-04-12.md
promotion_to: 04 stm/research-notes/observatory-trial/instrument-calibration.md
promotion_reason: the result changes the next trial step
```

Keep observed facts separate from derived conclusions. If the source is mutable, store the observation date and reverify before current use.

## Adjacent promotion ladder

```text
06 ISTM -> 05 Daily -> 04 STM -> 03 MTM -> 02 LTM -> 01 XLTM
```

Adjacent promotion provides a review boundary at each change in responsibility.

### ISTM to Daily

Convert raw events into a readable causal record. Preserve exact source pointers, timestamps, hashes when available, omissions, and redactions. Do not present generated summaries as raw observations.

### Daily to STM

Route meaningful episodes into detailed leaves. Preserve exact decisions, corrections, commands, dates, and evidence limits that can affect later reconstruction.

### STM to MTM

Consolidate recurring or still-active episodes into a branch. Do not copy the whole chronology. Keep the source leaf inventory or pointers that support the branch state.

### MTM to LTM

Promote durable patterns and stable domain contracts. Exclude temporary chatter. Record why the pattern is expected to persist.

### LTM to XLTM

Promote only cross-domain invariants, top-level domain anchors, or long-horizon truths and preferences. Keep this boundary conservative.

### Structured memory to Life Archive

Archive selected reusable histories without removing the current structured owner. The archive preserves the narrative and provenance, while current state remains in the temporal ladder.

## Promotion is not truth elevation

A higher layer is more durable, not more correct. Promotion should never be triggered only because generated text repeated a claim. Promotion needs an accountable owner, source evidence, and a reuse reason.

When a promoted claim later changes, preserve the older dated observation and mark the new record as superseding it. Do not silently rewrite history into a timeless statement.

## Parent-first materialization

The hierarchy is mechanically useful only when ownership exists before detail.

1. XLTM permits or records the domain.
2. LTM materializes the domain tree.
3. MTM materializes the branch.
4. STM materializes the leaf.

A single maintenance pass may create the complete missing chain, but it creates parents before children and validates every touched path.

## Conflict handling

When evidence conflicts, keep the conflict visible.

- Record both source pointers.
- Identify whether the disagreement is factual, temporal, or interpretive.
- Mark any superseded observation with a concrete date.
- Do not use a confidence label to hide missing evidence.
- Keep downstream conclusions blocked when a material dependency is unresolved.

## Derived indexes

Index rows, embeddings, ranks, graph communities, and route scores are derived state. They can help retrieval, but they do not own provenance or promotion decisions. A rebuild must be possible from the canonical forest alone.
