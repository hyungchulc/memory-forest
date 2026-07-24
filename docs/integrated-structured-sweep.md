# Integrated Structured sweep

The reference workflow separates source capture from one integrated Structured
decision. Daily writes readable committed source only. A Structured model or
reviewer then sees the bounded Daily inputs together with the relevant current
XLTM, LTM, MTM, and STM bodies, decides every required change in one result, and
hands a closed plan to `apply-structured`.

This is not four independent STM, MTM, LTM, and XLTM jobs. It is also not a
leaf-only promotion that invents parents afterward. Parent-before-child is an
internal structural rule inside the one sweep.

## Layer decisions

| Layer | Object | Put information here when | Split bias |
|---|---|---|---|
| STM | leaf | meaningful detail must remain reconstructable, including one-off asks, named entities, corrections, episodes, exact state, failures, commands, or verification | active |
| MTM | branch | STM evidence repeats or remains alive as an ongoing project, interest, capability, concern, or reread lane | moderate |
| LTM | tree | a durable theme, stable concern, enduring preference cluster, or long-lived capability context improves future classification | conservative |
| XLTM | forest | an identity-level truth, persistent direction, strong preference, or repeated long-horizon axis must classify the trees below it | most conservative |

Promotion is not importance inflation. A critical but temporary incident can
remain STM. A modest pattern can belong in MTM or LTM when it is recurring and
structurally useful.

## When structure grows

Create a new STM leaf when a branch-root body would mix materially different
reread questions, or when a named entity, episode, concrete subtopic, repeated
question, exact number, deadline, payment, balance, administrative route,
correction, or source state deserves an exact retrieval target.

Create a new MTM branch when the current center and likely reread questions
separate from existing branches and the flow repeats, persists, or expects
follow-up. A branch may represent an interest or capability, not only a work
project.

Create a new LTM tree when a distinct durable axis would improve future
classification and prevent a broad existing tree from absorbing an independent
ecosystem.

Update the XLTM forest only when repeated strong evidence establishes a
long-horizon classification rule or anchor that the existing forest cannot
safely express.

If a clearly justified route is missing, the same sweep creates or replaces the
required XLTM forest authority, LTM tree, MTM branch, and STM leaf in canonical
order.
It must not create a child first. Generic remainder files are emergency
quarantine for genuinely ambiguous routing, not normal active write targets.

`tree` is the routing key used by LTM, MTM, and STM targets. It names the LTM
tree that owns the lower branch and leaf. It is not an extra level between the
forest and the tree.

Every committed Daily item receives exactly one disposition:

- `promoted`, with one or more targets changed in this sweep
- `already_covered`, with exact current targets that already contain the fact
- `source_only`, when the item should remain source evidence only
- `promotion_debt`, when the item is meaningful but a safe structured decision
  is concretely blocked

## Deterministic boundary

`structured-context` returns bounded current bodies with exact route and content
hashes plus one hash over every current XLTM/LTM/MTM/STM path and content hash.
`apply-structured` accepts only semantic layer targets, `create` or full-body
`replace`, exact replace preimages, complete Markdown bodies, source
dispositions, and provenance hashes. Raw filesystem paths, delete, move, and
arbitrary operations are rejected.

The writer acquires the shared maintenance lock, verifies Daily provenance, the
whole-forest Structured snapshot, and every changed target preimage, then stages
all layer changes as one transaction. It validates and audits the resulting
forest, rebuilds the local index once, and publishes one receipt. Any handled
failure rolls the whole sweep back. An exact receipt-backed retry verifies the
committed poststate instead of requiring the old preimage snapshot again.

The deterministic core enforces shape, ownership, provenance, and transaction
safety. The caller owns semantic judgment and must apply the layer rules above
when it constructs the plan.
