# Type safety: making the domain vocabulary un-mistypeable

> **Status: the plan for PR #15, agreed 2026-08-24. ALL FIVE STAGES SHIPPED.** An earlier revision
> claimed stage 5 had landed "as fallout" before it had; that entry now records why the compiler
> could not have done it, because the reason generalises. Two of stage 4's stated facts turned out to be wrong and
> are corrected in place below.** Supersedes the open-decision note
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

> **SHIPPED**, in four commits: `29f7ad5` (chain), `63c89c7` (the move), `83ba7a7` (the enum),
> `171afce` (the identifiers).

`ModuleName`, `ColumnKey`, `VariantName` as `NewType`. `MetricIdentity` moves here from
`app/graphql/context.py` — its own comment already anticipates that. `ScoreKey` and `_Rule` retype;
`ScoreKey.chain` becomes `ChainRole | None` and `_CHAIN_SUFFIX` is built from the enum's values.

### 4 — `app/models/orm.py`

> **SHIPPED** (`d1f51e0`). It was worth doing, and cost three annotations. Two things this entry
> said were wrong; both are corrected below rather than deleted, because the corrections are the
> useful part.

`Mapped[ColumnKey]`, `Mapped[ModuleName]`, `Mapped[VariantName | None]`. **This is the stage that
decides whether the exercise is worth doing.** Leave the ORM as `Mapped[str]` and every read needs
`ColumnKey(metric.column_key)` at the boundary — the friction that gets `NewType` abandoned six
months later. Retype it and the alias flows outward for free. It does: `metric_identity_from_db_metric`
is now four reads and no conversion.

**CORRECTION 1 — this cannot import the aliases from `keys.py`.** That closes a cycle, because
`keys.py` imports the ORM back for `ChainRole`, and the application stops importing entirely. The
three aliases therefore live in `app/catalog/identifiers.py`, a leaf, exactly as `variant_kind.py`
does. `TYPE_CHECKING` does NOT rescue it — SQLAlchemy resolves `Mapped[...]` at class creation and
raises `MappedAnnotationError`; the full elimination is in that file's docstring.
`tests/test_import_graph.py` now guards the whole graph.

**CORRECTION 2 — no `type_annotation_map` entries are needed.** `Mapped[NewType]` is deprecated only
on a BARE annotation, where it also silently drops the length:

```
Mapped[ColumnKey] + mapped_column(String(128))  ->  VARCHAR(128), no warning
Mapped[ColumnKey] bare                          ->  VARCHAR, SADeprecationWarning
```

Every column here is explicit. `views.py` uses bare annotations, which is the one place the map
would be needed — and it is out of scope (below).

The concrete case, as it stands in `app/graphql/context.py` today:

```python
return (
    ModuleName(metric.module.name),      # Mapped[str]
    ColumnKey(metric.column_key),        # Mapped[str]
    metric.variant_kind,                 # Mapped[VariantKind | None] — no cast needed
    VariantName(metric.variant) if metric.variant is not None else None,
)
```

The one field needing no cast is the one whose ORM type is already right. That is the argument in
four lines. It is currently ONE function; the question is whether it stays one.

`app/models/views.py` has `interface_kind: Mapped[str]`, which is an `InterfaceKind` value and one
of the 11.

### 5 — The consumers

> **SHIPPED** (`5be2e74`). An earlier revision of this entry claimed it had landed "as fallout"
> from stages 3 and 4, before it had. That was wrong, and the correction is the useful part.
>
> mypy DID name every consumer where a change made something an error — a wide value arriving in a
> narrow slot. It said nothing about the opposite direction, which is legal and lossy:
>
> ```python
> def narrowing_is_silent(m: ModuleName) -> MetricKey:
>     return MetricKey(m, "col", None, None, (), ())   # Success: no issues found
> ```
>
> `ModuleName` IS a `str`, so a `str` slot accepts one and simply forgets. Almost all of stage 5 is
> that shape, which is precisely why it was given its own stage rather than left to the compiler.

**The drift the plan warned about has happened.** `scripts/extract_score_keys.MetricKey` is
documented as mirroring `ScoreKey`, and three of its four identity fields no longer do:

| field | `ScoreKey` | `MetricKey` |
|---|---|---|
| `module` | `ModuleName` | `str` |
| `column_key` | `ColumnKey` | `str` |
| `variant_kind` | `VariantKind \| None` | `VariantKind \| None` |
| `variant` | `VariantName \| None` | `str \| None` |

Nothing enforces the mirror, which is the second half of the problem.

**THE ANSWER TO "DO THE ALIASES REACH `scripts/`?" IS: BY ROLE, NOT BY DIRECTORY.**

* A script type that MIRRORS an app-side type follows it. `MetricKey` now carries `ModuleName`,
  `ColumnKey` and `VariantName`, and `tests/test_extract_score_keys.py` pins the mirror with
  `get_type_hints` — resolved objects, not the raw string annotations, which would compare equal on
  spelling alone.
* Local seeder plumbing stays `str`. `recipe_name(modules: list[str])` and
  `_register_module(name: str)` are where a raw name ENTERS from a CSV header or a seed file, which
  is the boundary the aliases exist to have. Both now say so at the signature, because a reader who
  has seen `ModuleName` everywhere else will otherwise assume it was missed.

The mirror test was the missing half. Reverting `MetricKey` to bare `str` — the exact drift that
shipped — leaves `mypy .` reporting *"Success: no issues found in 70 source files"* and turns three
of its assertions red.

`app/graphql/context.py` (`MetricIdentity` users), `scripts/extract_score_keys.py` (which mirrors
`ScoreKey` and must not drift from it), the seeders, and the tests.

---

## Still open

* **`app/models/views.py`'s `interface_kind` is still `Mapped[str]`**, and it is an `InterfaceKind`
  value. Deliberately left: `app/routers/demo.py` reads it as a grouping key and as template data,
  so typing it would coerce reads to enum members and change a working page. It is also an *enum*
  question rather than a `NewType` one, and it is the one place a `type_annotation_map` entry would
  actually be required.
* **The type system does not cover three specific doorways**, all found by measuring during this
  work rather than by reasoning: raw SQL binds (`text()` parameters are an untyped dict), `sorted()`
  and friends (which accept anything and fail at runtime), and ORM construction (declarative
  `__init__` is `**kw: Any`, so `db.Module(name="literal")` typechecks while
  `m.column_key = "literal"` does not). Every one is a value crossing into something dynamically
  typed. Nothing here closes them.
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
