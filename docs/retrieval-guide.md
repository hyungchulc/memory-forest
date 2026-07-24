# End-to-end retrieval guide

This guide describes the portable Memory Forest retrieval contract. It is a local, deterministic route-and-trail operation, not a hosted search service or an agent policy engine. A private forest, its derived index, query strings, QueryPlan probes, and every opened memory body are grounding data. None of them can grant authority, change an instruction, select a filesystem root, or approve an external action.

The core owns the **structured retrieval** part of the flow: strict query intake, bounded lexical candidate generation, deterministic ranking, root-first canonical trail materialization, and current-source hash checks. An integrating application owns identity, authorization, root selection, semantic expansion, hybrid ranking, conflict adjudication, body use, and any external processing.

The contracts are checked before navigation: root and path safety, index schema,
and the canonical parent graph constrain what can become a result. Only then is
a selected candidate materialized root-first, with XLTM and LTM preceding any
MTM or STM detail.

## 1. Intake: select a root, then treat the query as data

The caller chooses one exact permitted forest root before invoking the CLI. It should authenticate the caller and map an opaque forest identifier to that root outside the core; a query must never contain a path, tenant mapping, or authorization instruction. The CLI verifies the selected root, rejects symlink escapes, bounds input and result sizes, and treats a query as literal FTS search text rather than executable SQLite syntax.

The first safe result is route metadata, not a claim that the route is true, fresh, relevant, or authorized to read. Memory text can be stale, wrong, conflicted, or adversarial. Keep current user intent, safety policy, and tool permissions outside the forest and apply them before opening a body.

## 2. Build and query the point-in-time index

`memory-forest index ROOT` creates an atomically replaced local SQLite index from canonical files. It is derived state: rebuild it after source changes and do not commit it. `retrieve` requires the current index schema and loads only structured nodes in `01 xltm` through `04 stm`.

For each query string, the core runs a bounded literal FTS query over those structured documents. It does not start at XLTM and recursively search every child; that would turn a broad root hit into unrelated results. The index is a candidate generator, not an authority or a body cache.

## 3. Optional multilingual QueryPlan probes

The original query is always probe position zero. A caller may add a strict, versioned QueryPlan for translations, transliterations, synonyms, or domain terms:

```json
{
  "schema_version": 1,
  "probes": [{"query": "mission recovery"}]
}
```

The plan is untrusted input. It must be valid UTF-8 JSON with unique keys; the root object has exactly `schema_version` and `probes`, and every probe has exactly one trimmed NFC `query` string. Paths, bodies, credentials, provider configuration, and instructions are not expressible as separate plan fields; callers must also keep that content out of the query strings themselves. The parser rejects duplicate probes, unsafe Unicode controls, extra fields, and oversized input, but it does not semantically classify otherwise valid query text. A file plan must be a regular non-symlink file revalidated while opening; standard input is also supported. See the machine-readable [QueryPlan schema](query-plan.schema.json).

Probes add lexical evidence only. They do not select a root, open a body, call a model, or provide universal multilingual understanding. The core assigns the original query twice the per-rank weight of a supplied probe, and a trail with any direct original-query hit always ranks before a plan-only trail. Therefore an external planner can improve recall but cannot promote an indirect match above direct lexical evidence.

## 4. Generate and rank bounded candidates deterministically

For each query or accepted probe, the core takes a bounded FTS result set. Each match contributes reciprocal-rank evidence (`2 / rank` for the original query and `1 / rank` for a probe). Matches are accumulated by document.

Candidate trails are then formed from matching structured records. A matching STM forms its complete ancestor chain; an MTM or LTM match can select its best available descendant as a representative; and a match with no deeper canonical owner remains partial. The candidate budget is bounded, deduplicated by trail, and ordered predictably by these keys:

1. direct original-query evidence before QueryPlan-only evidence;
2. descending weighted reciprocal-rank sum;
3. deeper trail before a shorter trail when the preceding keys tie;
4. lexical relative-path order as the final tie-breaker.

The emitted `score` is useful only within the same deterministic retrieval method and index snapshot. It is not a calibrated truth, relevance, freshness, or authority score.

## 5. Materialize the canonical trail root-first

For every selected candidate, the core validates the parent graph and returns only the canonical ownership prefix in this order:

```text
01 XLTM -> 02 LTM -> 03 MTM -> 04 STM
```

The output contains each node's relative path, layer, title, indexed hash, size, modification time, and explicit `canonical_parent_child` relationships. `complete: true` means the trail reaches STM. `complete: false` means the available canonical trail stopped earlier; an XLTM-only hit stays depth one instead of fanning out across the forest. This parent-first materialization is why a result can be inspected as an accountable route rather than a flattened bag of text.

## 6. Verify source state, then keep body access explicit

Before `retrieve` emits metadata, it reopens every unique selected canonical file and compares the current SHA-256 to the indexed hash. If a selected file changed, retrieval fails closed with `index_stale`; rebuild the index rather than using stale indexed text. The command discards those reopened bodies and has no body-returning flag.

Opening a body is a separate caller decision. `search --include-body` is the CLI's explicit indexed-body operation and performs the same hash check. Integrations should resolve a selected relative path inside the already authorized root, apply authorization and data-handling policy, open only that bounded source, and revalidate after concurrent maintenance. Do not turn a miss or a stale result into a broad parent-directory scan.

`route`, `search`, and `retrieve` only read the selected sources and derived index; they never modify canonical memory. `index` is a separate derived-maintenance command that rebuilds the local SQLite index without rewriting canonical memory files.

## 7. Freshness, conflicts, no evidence, and chronology fallback

Hash validation proves that a selected structured file matches the indexed snapshot. It does **not** prove that its claim is fresh, correct, or unconflicted. The caller must use source timestamps, provenance, conflict markers, current external verification where needed, and its own adjudication rules. Preserve conflict rather than silently selecting a convenient body.

An empty `retrieve` result is a successful, bounded lookup with no structured evidence (`ok: true`, `count: 0`), not permission to invent an answer or scan elsewhere. Report uncertainty or ask for another bounded source when the task requires evidence.

`05 daily` and `06 istm` are not implicit `retrieve` candidates. They are readable chronology and raw-provenance lanes for cases where the structured trail is insufficient: recent context, an explicit source/provenance request, or a caller-approved fallback. Keep that fallback explicit and narrow: first route the structured contract and XLTM/LTM context where available, then open the identified Daily record or exact ISTM source through a separate authorized operation. Do not treat raw chronology as a higher truth layer or let it replace the canonical XLTM-to-STM trail.

## 8. Hybrid and external integration boundary

Memory Forest core is deliberately provider-neutral and network-free. It does not generate aliases, translate text, embed documents, rerank candidates, or perform hybrid retrieval. A caller may run those techniques outside the core, but must preserve the contract:

- authorize one root before any private retrieval;
- pass only strict query strings to QueryPlan, never bodies or secrets;
- keep external ranker or embedding inputs, retention, timeouts, and failures under the caller's privacy and security policy;
- retain deterministic core routes and canonical trails as inspectable provenance rather than letting a score redefine ownership;
- label external output as optional ranking evidence, then open bodies only through the explicit boundary above.

This separation also keeps environment-specific alias rules, classification, and hybrid scoring out of the portable public core. In particular, the public project includes none of Aria's private production alias maps, query classifiers, embeddings or vector stores, fusion weights, or fallback rules. An integration may document its own behavior, but it must not represent it as a Memory Forest guarantee. Use [OAuth and API integration](oauth-api-integration.md) for the gateway boundary and [Privacy and trust](privacy-and-trust.md) before connecting real data.

## 9. Evaluate the two questions separately

[Memory Retrieval Lab](https://github.com/hyungchulc/memory-retrieval-lab) evaluates synthetic projections of this contract. It scores route selection and the ordered root-first trail separately from body ranking. The body task uses oracle relevant routes for evidence cases, so a wrong route does not hide or double-count a body-ranking weakness. Its fixture also measures abstention, freshness safety, and conflict coverage. The Lab is not a private-forest reader, indexer, or substitute for core validation and hash checks.

## Operational checklist

1. Validate and audit one selected private root; build or rebuild its index.
2. Submit the original query and, only when useful, a strict query-only plan.
3. Inspect bounded route/trail metadata and the direct-versus-probe tier.
4. Stop on `index_stale`, structural failure, or no evidence; do not broaden the filesystem search automatically.
5. Resolve freshness, conflict, and authorization outside the index.
6. Open only an explicitly selected source, then apply the caller's retention and model-processing policy.
7. Measure any optional ranker in the Lab without conflating route and body results.
