# The ScoreEntry chapter — the plan for PR #16

> **Status: the plan, written 2026-08-25 as PR #15 closed and merged. Not started.** PR #15 typed the domain
> vocabulary specifically so this could be built on something internally consistent; this is the work
> that was waiting. `docs/graphql-schema.md` is the committed contract and stays authoritative for
> WHAT the types are — this file is the order to build them in and the decisions that order forces.

**Where the API is now.** Four resolvers (`experiments`, `experiment`, `candidates`, `candidate`) and
seven SDL types. The spec has sixteen. Everything below is the gap.

```
built:     Candidate  Chain  ChainRole  Experiment  Project  Recipe  Target  JSON
missing:   ScoreEntry  Metric  Module  Concept  Artifact  BenchmarkResult
           Direction  MetricValueType  VariantKind  ModuleType  InterfaceKind
           Query.modules  Query.metrics  Candidate.scores  Candidate.interfaceKind
           Candidate.target  Candidate.artifacts  Mutation entirely
```

**The corpus this has to serve** (measured 2026-08-25): 14 candidates, 200 distinct score keys, 144
catalogued metrics, 9 of them INTERFACE, 14 `candidate_summary` rows.

---

## The keystone: `Context.interface_kinds()` does not exist

Two comments already refer to it as though it does — `app/graphql/context.py:100` and
`tests/test_context_catalog.py:174` — and CLAUDE.md gives it a design constraint. It is the
candidate-side half of the two-tier lookup, and nothing else can be built first.

`MetricCatalog` answers *"is this heading INTERFACE-qualified?"*. That is tier one. Tier two needs
*"and what did THIS candidate fold?"*, which is `candidate_summary.interface_kind` — a per-candidate
value, so it wants its own per-request memo.

**It must take its own lock.** From CLAUDE.md, and this is not stylistic:

> A method that needs a critical section of its own takes its OWN lock, never the session's.
> `interface_kinds()` wants exactly this shape — give it `_interface_kinds_lock`, not the session's.

`asyncio.Lock` is not reentrant. Reusing `_catalog_lock` or reaching for the session's lock produces
a task waiting on itself: no exception, no traceback, no timeout, while every other request on the
loop is served normally. `tests/test_context_catalog.py::TestTheTwoLocksAreSeparate` already guards
the two that exist, including a test that collapses them and asserts the hang; the third belongs
there.

**One query, not one per candidate.** 14 candidates today, but the resolver runs per request and
GraphQL will ask for the whole list. Load every candidate's interface kind in a single read, keyed
by candidate id.

---

## Then `ScoreEntry`, which is the chapter's reason to exist

`ScoreEntry` is backed by no table. Each one is built from one entry in a candidate's `scores` JSONB
bag plus the catalog row explaining it. This is where the finding the whole schema turns on finally
becomes API behaviour: `boltz2.protein_iptm` resolves to *HER2 binding confidence*, *heavy-light
pairing*, or *not an interface*, depending on which chains that candidate folded.

**The two-tier lookup must MOVE, not be copied.** It currently lives in
`tests/test_context_catalog.py::_identity_for`, written longhand, and its own docstring says why that
is temporary:

> It lives in the test for now because the resolver does not exist yet — when it does, this should be
> deleted and the test should call it instead, or the test stops guarding the real code path.

Right now that test guards a *copy* of the logic. Leaving it would be worse after the resolver exists
than before, because it would look like coverage.

### Decisions this forces

* **`ScoreEntry.metric` is nullable, and the resolver has to mean it.** 200 keys, 144 catalogued —
  so keys legitimately resolve to no metric, and `docs/graphql-schema.md` already decided they
  surface with `metric: null` rather than erroring. Worth measuring on the way in: how many of the
  200 actually miss, and are they all `_export` rows?
* **`value` is never coerced; `numericValue` parses defensively.** Both already specified. The bag
  stores text uncoerced by design, and `numericValue` is null unless the metric says FLOAT or INT.
* **Filters (`module`, `chain`, `concept`) run in Python, not SQL.** The bag belongs to one candidate
  and holds at most 200 keys; pushing the filter into a JSONB query would trade a readable loop for a
  query that has to re-derive `decompose()` in SQL — which is the thing `app/catalog/keys.py` exists
  to prevent.
* **`interfaceKind` enters the SDL with FOUR members**, including `NO_CHAINS_RECORDED`, which is a
  data-quality state rather than a kind of molecule. `docs/graphql-schema.md` argues that out; do not
  re-litigate it, and note it is deliberately NOT the same set as `InterfaceKind.scoreable()`, which
  drops it.

---

## Order

Each stage moves `schema.graphql`, and `tests/test_schema_snapshot.py` turns every one into a
reviewable diff. That was PR #15 stage 1's entire purpose and this is where it pays.

| | | |
|---|---|---|
| 1 | `Context.interface_kinds()` + its own lock | nothing else can start |
| 2 | `ScoreEntry`, `Candidate.scores`, the lookup moved out of the test | the chapter's point |
| 3 | `Metric`, `Module`, `Concept`, `Query.modules`, `Query.metrics` | what a ScoreEntry points AT |
| 4 | `Candidate.interfaceKind`, `.target`, `.artifacts` + the `Artifact` type | small, and `target` is a two-hop hoist |
| 5 | `Mutation.annotateCandidate` | the only write in the API |

Stage 4's `Artifact` closes the last "for now" comment in the codebase (`app/models/orm.py:451`,
"the placeholder hook for now"). Stage 2 closes the other (`tests/test_context_catalog.py:40`).

---

## Who writes what

The last three PRs were heavy on supervision and light on the owner's own keystrokes. This chapter
is the opposite by construction: the pieces below encode a JUDGEMENT, and per CLAUDE.md they are his
to write. The right shape is a scaffold with the spot marked and the trade-offs named — not a
finished function to review.

**HIS — the decision-carrying code:**

1. **The two-tier branch in the ScoreEntry resolver.** This is the ipTM finding turned into an `if`.
   `tests/test_context_catalog.py::_identity_for` is the longhand version to move, and it already
   contains the two judgements: that a missing `interface_kind` on an INTERFACE-qualified heading
   RAISES rather than building an identity that resolves to nothing, and that it raises rather than
   `assert`s, because `python -O` strips asserts and the silent failure is the exact thing the
   design exists to prevent.
2. **`numericValue`'s defensive parse.** What counts as parseable, and what a failure returns.
   `docs/graphql-schema.md` §"numericValue parses defensively, even though nothing currently fails"
   has the reasoning; the code is four lines and every one of them is a decision.
3. **The `ScoreEntry.metric` null fallback.** 200 keys, 144 catalogued. What a key with no catalog
   row surfaces as, and whether that is worth logging.
4. **The `scores(module:, chain:, concept:)` filter semantics** — in particular whether `chain`
   filters on the suffix `decompose()` stripped, which is the only place a ScoreEntry still knows it.

**MINE — the scaffolding around it:**

* `Context.interface_kinds()`: the query, the memo, `_interface_kinds_lock`, and the test that
  collapses it into `_catalog_lock` and asserts the hang (the existing
  `TestTheTwoLocksAreSeparate` is the pattern).
* The Strawberry type declarations and their wiring into `Query`/`Candidate`.
* Regenerating `schema.graphql` at each stage so every SDL change is a reviewable diff.
* Tests around his logic once its shape is settled — including deleting `_identity_for` from the
  test and repointing the assertions at the real resolver, so the test stops guarding a copy.

**A fair warning about stage 1.** `interface_kinds()` is raw SQL against `candidate_summary`, which
is doorway one below. The bind is an untyped dict and mypy will not check it. Worth an eyeball on
the bind names before it runs, not after.

---

## What PR #15 hands over, and what it did not close

Inherited, and all three are places a value crosses into something dynamically typed — none of which
this chapter fixes, and all of which it will touch:

* **Raw SQL binds.** `text()` parameters are an untyped dict. PR #15 shipped a bug through one.
  `interface_kinds()` will be raw SQL against the view.
* **`sorted()` and friends** accept anything and fail at runtime. The first review of PR #15 found one.
* **ORM construction.** Declarative `__init__` is `**kw: Any`, so `db.Module(name="literal")`
  typechecks while `m.column_key = "literal"` does not.

Still genuinely open from `docs/type-safety-plan.md`, and stage 4 above is where the first becomes
a live question:

* `app/models/views.py`'s `interface_kind` is `Mapped[str]` and is an `InterfaceKind` value. Typing
  it would coerce reads to enum members and change `app/routers/demo.py`, which reads it as a
  grouping key. **`Context.interface_kinds()` is the second consumer**, so decide it in stage 1
  rather than inheriting two spellings.
* `Concept.name`, `Experiment.name` and `Module.name` all mean different things and only `Module.name`
  is typed. Stage 3 introduces `Concept` to the API and will ask again.

## How to know it worked

Not "the resolvers return data". It is whether this query returns three DIFFERENT display names for
one JSONB key, chosen by what each candidate folded:

```graphql
{ candidates { sequenceId interfaceKind
    scores(module: "boltz2") { key value metric { displayName } } } }
```

That is the 2026-07-31 finding, served over HTTP. Nothing in the API demonstrates it today.
