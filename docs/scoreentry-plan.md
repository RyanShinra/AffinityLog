# The ScoreEntry chapter — the plan for PR #16

> **Status: ALL FIVE STAGES SHIPPED on `score-entry-resolvers` (2026-09-18). Ready for PR #16.**
> Written 2026-08-25 as PR #15 closed, and kept up to date since — the decisions each stage forced
> are recorded in "What each stage decided" below, because the commits carry the reasoning
> but this file is what a cold session reads. `docs/graphql-schema.md` is the committed contract and
> stays authoritative for WHAT the types are; this file is the order and the decisions.

**Where the API is now.** Six root resolvers (`experiments`, `experiment`, `candidates`, `candidate`,
`modules`, `metrics`), `Candidate.scores(module:, chain:, concept:)`, `.interfaceKind`, `.target`,
`.artifacts`, `Mutation.annotateCandidate`, and twenty SDL types. The chapter's success criterion is
met with the plan's query VERBATIM: one JSONB key returns three display names beside three interface
kinds in one response (`tests/test_candidate_fields.py::TestInterfaceKind::test_the_finding_in_one_query`).
Of the committed spec, only `Metric.transformOf` and `Recipe.modules` remain unbuilt, each by decision.

```
built:     Candidate  Chain  ChainRole  Experiment  Project  Recipe  Target  JSON
           ScoreEntry  Metric  Module  Concept  BenchmarkResult  Artifact
           Direction  MetricValueType  VariantKind  ModuleType  ModuleFunction  InterfaceKind
           Query.modules  Query.metrics  Candidate.scores  Candidate.interfaceKind
           Candidate.target  Candidate.artifacts  Mutation.annotateCandidate
missing:   nothing this chapter set out to build
```

**The corpus this has to serve** (measured 2026-08-25): 14 candidates, 200 distinct score keys, 144
catalogued metrics, 9 of them INTERFACE, 14 `candidate_summary` rows.

---

## The keystone: `Context.interface_kinds()` — SHIPPED `12d6d28`

It is the candidate-side half of the two-tier lookup, and nothing else could be built first. Two
comments already referred to it as though it existed, and CLAUDE.md gave it a design constraint,
before any of it was written. What follows is why it has the shape it has.

`MetricCatalog` answers *"is this heading INTERFACE-qualified?"*. That is tier one. Tier two needs
*"and what did THIS candidate fold?"*, which is `candidate_summary.interface_kind` — a per-candidate
value, so it wants its own per-request memo.

**It must take its own lock.** From CLAUDE.md, and this is not stylistic:

> A method that needs a critical section of its own takes its OWN lock, never the session's.
> `interface_kinds()` wants exactly this shape — give it `_interface_kinds_lock`, not the session's.

`asyncio.Lock` is not reentrant. Reusing `_catalog_lock` or reaching for the session's lock produces
a task waiting on itself: no exception, no traceback, no timeout, while every other request on the
loop is served normally. `tests/test_context_catalog.py::TestTheLocksAreSeparate` already guards
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

**The two-tier lookup had to MOVE, not be copied — done in `f786597`/`b3b6c64`.** It lived in
`tests/test_context_catalog.py::_identity_for`, written longhand, and its own docstring said why that
was temporary:

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

| | | | |
|---|---|---|---|
| 1 | `Context.interface_kinds()` + its own lock | nothing else can start | **shipped** `12d6d28` |
| 1b | `Heading`, `MetricIdentity`, `VariantAxes`, `MetricCatalog.metric_for` | the lookup moved out of the test | **shipped** `f786597`, `b3b6c64` |
| 3 | `Metric`, `Module`, `Concept`, `BenchmarkResult`, `Query.modules`, `Query.metrics` | what a ScoreEntry points AT | **shipped** `120d16d` |
| 2 | `ScoreEntry`, `Candidate.scores` | the chapter's point | **shipped** 2026-09-18 |
| 4 | `Candidate.interfaceKind`, `.target`, `.artifacts` + the `Artifact` type | small, and `target` is a two-hop hoist | **shipped** 2026-09-18. ~~`artifacts` ships EMPTY~~ — **that premise was wrong**, see the stage 4 notes: the table holds 11 rows, written by `scripts/seed_corpus_context.py`. |
| 5 | `Mutation.annotateCandidate` | the only write in the API | **shipped** 2026-09-18 |

**STAGES 2 AND 3 WERE SWAPPED, on 2026-09-17, and the numbers above are left as they were rather
than renumbered** — the commits reference them. `ScoreEntry.metric` points at `Metric`, and
`ScoreEntry.numericValue` is specified as "null unless `metric.valueType` is FLOAT or INT", so
stage 2 cannot be built without at least part of stage 3. Building the pointer first would have
meant publishing a partial `Metric` in the SDL and growing it a commit later — a public contract
changing twice for no reason. Stage 3 also turned out cheaper than this plan implied: `_load_catalog`
already eager-loads `module`, `concept`, `benchmark_results` and `transform_of`, so it was type
declarations over data already in memory, with no new queries.

Stage 1 also grew a half. The two-tier lookup had to leave `tests/test_context_catalog.py` before
anything could call it, and doing that properly meant naming the types it was assembled from — see
"What stage 1b decided" below.

Stage 4's `Artifact` closed the last "for now" comment in the codebase (`db.Artifact`'s docstring,
"the placeholder hook for now"). Stage 1b closed the other (`tests/test_context_catalog.py:40`).

---

## What each stage decided

Recorded here because the commits carry the reasoning but the plan is what a cold session reads.

### Stage 1 — `interface_kinds()`

* **Keyed by `candidates.sequence_id`**, because that is what `candidate_summary` publishes (as
  `candidate_id`). That key is unique only PER EXPERIMENT — `uq_candidate_seq` is the composite —
  while the map spans all nine, so two colliding rows would hand one candidate the other's interface
  kind. Measured: 14 candidates, 14 distinct ids, and the two same-antibody pairs in the corpus
  carry DIFFERENT vendor ids across runs. A guard raises rather than folding them; proved by forcing
  a collision inside a rolled-back transaction. Adding `c.id` to the view would remove the question
  structurally, and is the remedy the error message names.
* **Both failures are `GraphQLError` with a code, not `ValueError`.** Neither is transient: once the
  data or the code is in that state, every query touching scores fails identically until a human
  intervenes. So each has two audiences — the operator who fixes it and the client who needs to know
  what broke — and `MaskInternalErrors` replaces the message of anything without a `code`.

### Stage 1b — the lookup's vocabulary

* `Heading`, `MetricIdentity` (promoted from a bare tuple alias) and `VariantAxes` exist to make
  `metric_for` readable, not to add behaviour. `Mapping[tuple[ModuleName, ColumnKey],
  frozenset[VariantKind]]` costs a reader ten seconds; `if axes.interface_qualified` says what is
  being asked where `if VariantKind.INTERFACE in kinds` did not.
* **Promoting `MetricIdentity` to a class turned three of four defects into compile errors.** The
  fourth — `.get()` with no default falling through to the raise for every ordinary key — was
  invisible to mypy and caught by tests written first.
* `metric_for` lives on `MetricCatalog` rather than in the resolver: it reads
  `variant_axes_per_heading` and produces a key into `metric_by_identity`, both of which are that
  class's own fields, and nowhere else can then reimplement the branch wrongly.

### Stage 3 — the catalog types

* **`Module.functions` is `[ModuleFunction!]!`, departing from the committed spec's `[String!]!`.**
  The column is `ARRAY(Enum(ModuleFunction))` and `moduleType` beside it is already an enum. The JSON
  a client receives is identical, so the difference is entirely contract: introspection shows the
  closed set, a future `modules(function:)` filter is validated before a resolver runs, and adding a
  member is already a migration `tests/test_postgres_enum_labels.py` enforces.
  `docs/graphql-schema.md` was updated to match.
* **`Query.metrics` reads the per-request catalog memo**, so it cannot disagree with the ScoreEntry
  lookup about what a metric is, and the four eager loads are written once. `Query.modules` needs its
  own `select`: a module that emits nothing is not in the catalog at all.
* **`Module.metrics` is a resolver, not a field.** Structural, not stylistic: `Metric.from_row`
  builds its `Module`, so a `Module.from_row` that built its metrics would recurse without end. It
  reads `metrics_per_module`, grouped once when the catalog is built.
* **`Metric.transformOf` is NOT exposed, though the spec lists it.** Nothing in the repo writes
  `transform_of_metric_id`, and `selectinload` loads exactly one level, so recursing `from_row` into
  the parent touches ITS unloaded `module` and raises `MissingGreenlet`. When something populates the
  column, the shape is a resolver over a by-id index. A test asserts the ABSENCE so the gap reads as
  a decision rather than an oversight.
* **A Strawberry enum binding cannot be used as a type annotation.** `strawberry.enum` returns
  `EnumType | Callable[[EnumType], EnumType]` — a union serving both decorator forms — so
  `module_type: ModuleType` is rejected. It REGISTERS the class and returns it unchanged, so the
  annotation to write is `db.ModuleType`. Those module-level lines are registration statements, not
  aliases. `Chain.role` has had the right shape since it was written.

### Stage 2 — `ScoreEntry` and `Candidate.scores`

Built copy-paste style: every line shown in chat and interrogated before it went in, tests included.
The four judgement calls, and where each landed:

* **`numericValue`** (`_numeric_value` in `app/graphql/types.py`): FLOAT and INT only, so a BOOL
  stored as `"1"` is never served as a measurement. Catches `ValueError` only — `value` is `str`
  all the way down, so a `TypeError` there is our bug and must surface. A parse failure logs at
  DEBUG, the first logger in `app/`: a censored value under a numeric metric is a signal, not an
  alarm. `tests/test_score_entry.py::TestNumericValue` pins all three.
* **`metric: null`**: `metric_for` already returns `None` on a miss and the resolver passes it
  through. No log line — on the real corpus every key resolves, and the seeder's `--dry-run` is
  the tool for "what is new in this CSV", not a resolver firing 1132 times a request.
* **`module` filters on the KEY's prefix**, `score_key.module`, not on `metric.module.name`. The
  two agree for every catalogued key and differ only for an uncatalogued one, which has no metric
  to match: matching the key keeps it, so `module: "mystery"` finds `mystery.column` whether or
  not the catalog knows it. `concept` has no such choice and drops uncatalogued keys.
* **`chain: null` means no filter**, two-valued. The three-valued version (`strawberry.UNSET` so
  that `null` could mean "unsuffixed entries only") was considered and rejected: nothing asks for
  it, and a client can read `chain == null` off the response.
* **Entries are sorted by key.** JSONB does not preserve insertion order, so without this the
  list order would follow Postgres's key hashing and the snapshot-style tests would be flaky.
* **The filters and the parse are module functions**, `_passes_filters` and `_numeric_value`,
  so they are unit-testable with an unflushed `db.Metric` and no session. That is the ORM
  construction doorway from `docs/type-safety-plan.md`: `**kw: Any`, so the keyword names in
  those tests get no checking.

The fixture in `tests/conftest.py` grew a seventh metric (`temstapro.verdict`, CATEGORICAL) and
four keys per candidate, one per resolver branch: a chain suffix, a censored numeric, a
categorical that looks numeric, and a key with no catalog row.

**Found along the way:** `migrations/env.py`'s `fileConfig` was disabling every existing logger,
because `disable_existing_loggers` defaults to True and `tests/conftest.py` runs the migrations
in-process after the app is imported. A caplog test passed alone and failed in the suite. The
server never hit it — docker-compose runs Alembic as its own process — but any future `app.*`
logger would have been silent under pytest. One keyword.

### Stage 4 — `interfaceKind`, `target`, `artifacts`

Shown in chat first, then written in by Claude at the owner's request. Three fields, three decisions:

* **`interfaceKind` is non-null, so absence RAISES.** A non-null field that raises nulls the entire
  `candidates` response, not just the `Candidate` — this said otherwise until the review corrected
  it, and whether raising still holds up is deferred to issue #17. A candidate missing from
  `candidate_summary` is a coded
  `GraphQLError` (`CANDIDATE_NOT_IN_SUMMARY`) from `_interface_kind_of`, a module function testable
  with an empty map. The `scores` resolver deliberately does NOT use it: an absent candidate can
  still resolve every non-INTERFACE heading, and `metric_for` raises only for the ones it cannot.
  The fourth member, `NO_CHAINS_RECORDED`, is proved reachable by a test that inserts a chainless
  candidate.
* **`target` is ONE join per candidate that asks**, `select(Target).join(Experiment)` on the
  experiment id — not `Experiment.select_statement()` filtered by id, which carries three eager
  loads and is why `candidates { experiment { name } }` measured at 58 queries. A client asking for
  both `experiment` and `target` pays for the experiment twice; that is the cheaper trade.
* **`artifacts` is a resolver, not an eager load** — a `selectinload` would add a query to every
  candidate read whether or not the client asked. **The stage was planned and first written on
  a false premise: "nothing writes the `artifacts` table, the structures live only on disk."**
  Wrong. `scripts/seed_corpus_context.py` — in CLAUDE.md's rebuild sequence — registers every
  `<candidate id>_<tool>.pdb` as a row, `kind` = the tool (`boltz2`, `rfantibody`), `uri` = the
  repo-relative path; the grep that missed it looked for `Artifact(` and the seeder inserts with
  raw SQL. Caught by running the finished resolvers against the real corpus: 11 artifacts, not 0.
  The code needed no change; three docstrings, the ORM's `kind` comment ("structure / sequence",
  also wrong) and this doc did. The fixture still seeds no artifact rows, so the mapping test
  inserts one. `kind` is a `String(32)` holding a closed set of tool names, which is the
  type-safety plan's enum-plus-migration shape, and is now a live question rather than a
  hypothetical one.
* **`InterfaceKind` is registered under a different binding name** (`InterfaceKindEnum`) from the
  six others. Those rebind a name only reached through `db.`; this one is imported by its own name
  and used as an annotation, and rebinding it would replace the class with the registration's
  return value.

Settled without reopening: `app/models/views.py`'s `interface_kind` stays `Mapped[str]`. Stage 1
converts string to enum at the SQL boundary inside `_load_interface_kinds`, and `demo.py` keeps
reading the view as a grouping key.

### Stage 5 — `Mutation.annotateCandidate`

* **The write goes through the lock.** `TaskSafeSession` grew its second method, `commit()`, in
  the shape the first one set: take the lock, do the one thing, release. Its docstring's NOT A
  PROXY section had predicted exactly this. `Context.commit()` is the one-line door, so the
  mutation reads through `execute_statement` and writes through `commit` and can reach nothing
  else. `tests/test_mutation.py::TestCommitGoesThroughTheLock` holds the lock and watches
  `commit()` wait.
* **`""` clears.** `String!` argument, nullable column. A nullable argument with no default is
  also omittable, so `annotateCandidate(id: "x")` would clear silently; the required string with
  the empty string meaning "none" keeps the clear explicit. Recorded in `docs/graphql-schema.md`.
* **Unknown id is `NOT_FOUND`**, coded, distinct from `_as_uuid`'s `BAD_USER_INPUT`. The return
  type is non-null, so null was never available.
* The ORM attribute is set outside the lock, and that is safe by the GraphQL spec rather than by
  luck: mutation root fields execute serially, and the returned Candidate's resolvers run only
  after the mutation returns.

### Found along the way in stage 3, and fixed

`MaskInternalErrors` was masking GraphQL's own syntax and validation errors, so every client typo
came back as "Internal server error." Found because all fourteen stage-3 tests failed with that
message instead of "Cannot query field 'metrics'". The discriminator is `original_error`, which
GraphQL's own errors do not carry. See `1695043` and `f7f0f61`.

---

## Who writes what

The last three PRs were heavy on supervision and light on the owner's own keystrokes. This chapter
is the opposite by construction: the pieces below encode a JUDGEMENT, and per CLAUDE.md they are his
to write. The right shape is a scaffold with the spot marked and the trade-offs named — not a
finished function to review.

**HIS — the decision-carrying code:**

1. ~~**The two-tier branch in the ScoreEntry resolver.**~~ **DONE in stage 1b**, and it landed as
   `MetricCatalog.metric_for` rather than inside the resolver — see "Stage 1b" above for why the
   catalog owns it. Both judgements survived the move: a missing `interface_kind` on an
   INTERFACE-qualified heading RAISES rather than building an identity that resolves to nothing, and
   it raises rather than `assert`s, because `python -O` strips asserts and the silent failure is the
   exact thing the design exists to prevent. What remains for the resolver is the FILTER semantics —
   whether `module: "boltz2"` matches the key's prefix or `metric.module.name`, which differ for the
   two `_export` keys.
2. ~~**`numericValue`'s defensive parse.**~~ **DONE in stage 2** — see above.
3. ~~**The `ScoreEntry.metric` null fallback.**~~ **DONE in stage 2** — passed through, unlogged.
4. ~~**The `scores(module:, chain:, concept:)` filter semantics**~~ **DONE in stage 2** — `chain`
   does filter on the stripped suffix, via `ScoreKey.chain`.

**MINE — the scaffolding around it:**

* `Context.interface_kinds()`: the query, the memo, `_interface_kinds_lock`, and the test that
  collapses it into `_catalog_lock` and asserts the hang (the existing
  `TestTheLocksAreSeparate` is the pattern).
* The Strawberry type declarations and their wiring into `Query`/`Candidate`.
* Regenerating `schema.graphql` at each stage so every SDL change is a reviewable diff.
* ~~Tests around his logic, including deleting `_identity_for`~~ — **done in `f786597`**, which
  wrote the spec first and deleted the copy, so `TestTheIptmFinding` now calls `metric_for` rather
  than asserting the finding against a function defined in the test file.

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

That is the 2026-07-31 finding, served over HTTP. **It does, as of stage 4.**
`tests/test_candidate_fields.py::TestInterfaceKind::test_the_finding_in_one_query` runs exactly that
query against the fixture and asserts the three (kind, name) pairs.
