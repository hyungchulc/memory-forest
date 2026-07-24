# Layer contracts

The numbered directories assign ownership and expected lifetime. They do not assign truth rank.

## 00 Life Archive

**Object** - archive-grade reusable history.

Use this layer for selected project histories, long narratives, or reference records that deserve preservation outside the active temporal ladder. An archive record keeps provenance and may summarize several structured layers.

Do not use Life Archive as a dumping ground or as a replacement for current state. Current facts still belong in the temporal ladder and still require freshness checks.

## 01 XLTM

**Object** - forest.

XLTM owns the forest map, persistent cross-tree invariants, and strong long-horizon preferences or constraints. It is the first node in each materialized root-first retrieval trail.

Keep this layer compact. A tactical instruction, one-off fact, or temporary state does not belong here merely because it feels important.

## 02 LTM

**Object** - durable tree.

LTM owns durable knowledge, stable tree contracts, and long-lived thematic bodies. A tree exists when a topic is stable enough to improve future classification and rereading.

LTM should point upward to XLTM and downward to its MTM branches. Detailed incidents remain in lower layers.

## 03 MTM

**Object** - active recurring branch.

MTM owns ongoing projects, repeated procedures, active concerns, and medium-horizon operating state. It compresses recurring STM evidence into a current branch without copying every episode.

A branch can represent recurring interests or capabilities, not only work projects.

## 04 STM

**Object** - detailed leaf.

STM owns dated, reconstructable detail. It is the normal home for meaningful short-term facts, implementation evidence, corrections, exact decisions, and one-off events that can affect a later answer.

STM is detailed but not raw. Preserve enough source context to reproduce the conclusion and point back to Daily or ISTM when exact chronology matters.

## 05 Daily

**Object** - readable source lane.

Daily turns recent admitted source events into a readable causal record. It preserves what happened, when it happened, and where the exact source can be recovered without copying every raw transport or tool payload.

Daily is chronology and provenance, not the final structured owner. Durable meaning should be promoted to STM and above.

## 06 ISTM

**Object** - raw chronology and provenance.

ISTM is append-oriented source evidence. It can hold exact event identifiers, timestamps, hashes, source cursors, and raw or minimally transformed records. It is not the normal retrieval surface for an end user.

Raw events can contain untrusted instructions, secrets, or personal data. Keep this layer private and apply redaction and retention rules before wider use.

## Placement checklist

Before writing a record, answer these questions.

1. What tree owns it?
2. What recurring branch owns it?
3. What is the nearest real parent?
4. Is this raw source, readable chronology, detailed evidence, recurring state, durable knowledge, or a long-horizon anchor?
5. What provenance must survive promotion?
6. What would make the record stale, superseded, or unsafe to use?

If a durable parent does not yet exist, materialize the parent chain before the child. Do not create empty shells just to make the tree look complete.

## Promotion and split decisions

Run these decisions together in one Structured sweep over committed Daily and
the relevant current XLTM/LTM/MTM/STM bodies.

- Treat meaningful reconstructable detail as an STM candidate, including
  one-off asks, named entities, corrections, exact state, failures, and
  verification.
- Promote recurring and still-live STM flows to an MTM branch. Split a branch
  when its current center and reread questions are distinct from existing
  branches.
- Promote durable themes, stable concerns, enduring preference clusters, and
  long-lived capability context to an LTM tree. Tree splits are conservative.
- Promote only identity-level truths, persistent direction, strong preference,
  and repeated long-horizon classification axes to XLTM. Forest-level changes
  are the most conservative split.
- Split leaves actively, branches moderately, and trees conservatively.

If a clear route lacks parents, create the required authority chain in
parent-before-child order inside the same sweep. This ordering protects the
canonical graph; it is not a separate parent-first processing architecture.

See [Integrated Structured sweep](integrated-structured-sweep.md).

## Naming model

A portable forest follows these conventional shapes.

```text
01 xltm/XLTM.md
02 ltm/<tree>_LTM.md
03 mtm/<tree>/<branch>.md
04 stm/<tree>/<branch>/<leaf>.md
05 daily/YYYY-MM-DD.md
06 istm/events.jsonl
00 life_archive/<archive-record>.md
```

The CLI validates the selected root. Projects may add stricter naming rules while retaining the same layer ownership and path-safety contract.

Some legacy storage and retrieval surfaces call the tree routing key
`domain`. That label describes the on-disk grouping key only. It is not a
fifth structural object. The ontology remains Forest, Tree, Branch, Leaf.
