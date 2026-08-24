# Type safety: making the domain vocabulary un-mistypeable

> **Status: the plan for PR #15, agreed 2026-08-24. Stages 1 and 2 shipped; 3-5 open.** Supersedes the open-decision note
> that lived at `stringly-typed-catalog-note.md`. Read this before writing any of it — the sequencing
> is load-bearing, and several of the facts below took measuring rather than reasoning.

**Why now.** The remaining GraphQL work — `ScoreEntry`, the `Metric`/`Module`/`Concept` types,
`Candidate.scores`, and `Mutation` entirely — will be built on these types. Doing it after means
retyping code that has just been written; doing it now means the home stretch lands on something
internally consistent.

---

## The problem

The domain vocabulary is strings, and the type system has nothing to say about any of it:

```python
MetricIdentity = tuple[str, str, str | None, str | None]   # (module, column_key, variant_kind, variant)
```

Transpose the first two and mypy --strict is silent, the lookup misses, and the key resolves to no
metric — no exception, no log. That is the same failure mode the two-tier lookup, the write-time
invariant check, and migration 008's CHECK all exist to prevent, arriving through a door none of
them watch.

`variant_kinds_per_heading: Mapping[tuple[str, str], frozenset[str]]` is the sharpest case: three
different kinds of string in one annotation, and `(module, column)` is indistinguishable from
`(column, module)`.

## Inventory

Measured across `app/` and `scripts/` — 192 annotations mention `str`, of which 60 carry domain
meaning:

| category | count | the failure it permits |
|---|---|---|
| **closed set** — an enum exists or should | 11 | a misspelling typechecks and matches nothing |
| **identifier** — a `NewType` fits | 49 | a transposition typechecks and resolves to nothing |
| **free text** — leave alone | 18 | none; `description`, `notes`, `sequence`, `display_name` |

The remaining ~132 are file paths, CLI arguments and similar. Not in scope.

## The decision

**Real enums for the closed sets, not tagged strings.** A `NewType` would stop you passing a column
key where a kind belongs, but `VariantKindName("PARMETER")` typechecks fine and matches no catalog
row — silently, which is the whole problem. Only the enum closes that.

So `VariantKind` moves from `app/models/orm.py` to `app/catalog/`, and the ORM references it there.
That inverts today's dependency direction, and the checks below are why that is safe rather than
merely convenient.

`NewType` for the identifiers, where the values genuinely are open strings and only their ROLE is
closed.

## What was verified before committing to this

Do not re-derive these; they cost real time.

* **Moving `VariantKind` needs no migration.** `SAEnum(VariantKind).name` is `'variantkind'`,
  derived from the CLASS name, not the module. The Postgres type keeps its name.
* **No circular import.** `app/catalog/__init__.py` is docstring-only, so importing
  `app.catalog.variant_kind` does not drag in `invariants`. Load order becomes
  `invariants -> orm -> catalog.variant_kind (leaf)`. It is `__init__.py` staying import-free that
  makes this work; adding an import there would break it.
* **`Mapped[NewType]` works, but is deprecated unless registered.** SQLAlchemy warns
  ("add this type to the type_annotation_map") and the warning is on its way to becoming an error.
  Each alias needs an entry in `ModelBase.type_annotation_map`.
* **`NewType` cannot cross into Strawberry.** A bare `NewType` on a GraphQL field raises
  `TypeError: Unexpected type` and the schema fails to BUILD. Making it work needs
  `strawberry.scalar(...)`, which adds a scalar to the SDL — i.e. changes the public contract.
  Hence stage 1, and hence the GraphQL types staying out of scope.
* **`ChainRole` already exists** with `HEAVY = "H"`, `LIGHT = "L"`, `TARGET = "T"` — exactly the
  letters `_CHAIN_SUFFIX = re.compile(r"\.(H|L|T)$")` hardcodes, with nothing checking the two
  agree. `ScoreKey.chain` does not need a new enum; it needs the existing one, and the regex should
  be derived from it.

Two claims from the earlier draft of this note were wrong and are corrected here: `app/catalog/`
*does* import the ORM (`invariants.py:121`), so there was never a rule against it; and
`InterfaceKind` living in `app/catalog/` is NOT precedent for the ORM referencing a catalog enum —
nothing in `app/models/` imports it. The `VariantKind` move creates that direction for the first
time.

---

## Stages

Five commits. Each leaves `main` green and is independently reviewable. A 60-site retype landing as
one diff is unreviewable, and this project has spent a fortnight demonstrating what happens to
remediation nobody can read.

### 1 — Snapshot the SDL, before touching anything

> **SHIPPED** (`840eb2e`).

Nothing asserts on the printed schema. The TODO at `app/graphql/schema.py` says so:

> no test asserts on the printed SDL yet, so nothing currently checks this against the spec in
> `docs/graphql-schema.md`. `schema.as_str()` is the hook for one.

Until that exists, any type change that reached a Strawberry field would alter the public contract
invisibly. This is the safety net that makes every stage after it reviewable, and it closes a TODO
that predates this plan.

### 2 — `VariantKind` moves to `app/catalog/variant_kind.py`

> **SHIPPED** (`2b7a28c`).

Mechanical but wide: 47 references across 9 files, 28 of them in `test_catalog_invariants.py`. The
ORM imports it and keeps `SAEnum(VariantKind)`, so the database is untouched.

### 3 — The aliases, in `app/catalog/keys.py`

`ModuleName`, `ColumnKey`, `VariantName` as `NewType`. `MetricIdentity` moves here from
`app/graphql/context.py` — its own comment already anticipates that. `ScoreKey` and `_Rule` retype;
`ScoreKey.chain` becomes `ChainRole | None` and `_CHAIN_SUFFIX` is built from the enum's values.

### 4 — `app/models/orm.py`

`Mapped[ColumnKey]`, `Mapped[ModuleName]`, plus the `type_annotation_map` entries. **This is the
stage that decides whether the exercise is worth doing.** Leave the ORM as `Mapped[str]` and every
read needs `ColumnKey(metric.column_key)` at the boundary — the friction that gets `NewType`
abandoned six months later. Retype it and the alias flows outward for free.

`app/models/views.py` has `interface_kind: Mapped[str]`, which is an `InterfaceKind` value and one
of the 11.

### 5 — The consumers

`app/graphql/context.py` (`MetricIdentity` users), `scripts/extract_score_keys.py` (which mirrors
`ScoreKey` and must not drift from it), the seeders, and the tests.

---

## Still open

* Do the identifier aliases reach `scripts/`, or stop at the `app/` boundary? Scripts are where a
  raw string legitimately enters the system from a CSV.
* `Concept.name`, `Experiment.name`, `Module.name` are all `Mapped[str]` and all mean different
  things. One `Name` alias is useless; three is fussy. Undecided.
* Whether `strawberry.scalar` for the GraphQL surface is ever worth the SDL churn. Deliberately not
  answered here — revisit once the resolvers exist and there is something to protect.

## How to know it worked

The test is not "mypy passes" — it passes today. It is whether these two lines stop typechecking:

```python
metric_by_identity[(column_key, module, None, None)]      # transposed
decomposable_kinds_for(module, column_key) == {"PARMETER"}  # misspelt
```

The first is what `NewType` buys. The second is what only the real enum buys, and is why the
decision above went the way it did.
