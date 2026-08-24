# PR #15 diary — teaching the type system the vocabulary

> **Status: IN PROGRESS, updated as each stage lands.** Unlike the PR #13 and #14 diaries, this one
> is being written alongside the work rather than after it, so the later acts will be thinner than
> the earlier ones until they are done. `docs/type-safety-plan.md` is the plan and stays
> authoritative for *what* is going to happen; this file records *why it keeps changing shape*.

**Span:** 2026-08-24 → · **branch:** `type-safety` · 3 of 5 stages · 6 commits · 132 → 142 tests · `mypy .` clean

---

## Where it came from

Not from a bug. From an aside — a `/btw` conversation about whether the `str` in the catalog's key
annotations wanted to be a strong typedef. That got written down as an open-decision note and then
sat, because the cleanup branch owned the schedule.

It came back for a specific reason. The remaining GraphQL work — `ScoreEntry`, the
`Metric`/`Module`/`Concept` types, `Candidate.scores`, and `Mutation` entirely — is the largest
block of code still unwritten, and all of it consumes this vocabulary. Doing the typing afterwards
means retyping code that has just been written. The owner's framing:

> so that our home stretch on graphQL is as stable as possible

Which also settled the scope. The note had been about the catalog; the answer was *"I really meant
the whole code base, so that when we later develop the GraphQL SDL it's built on something sturdy
and internally consistent."*

## The problem, in one line

```python
MetricIdentity = tuple[str, str, str | None, str | None]   # (module, column_key, variant_kind, variant)
```

Transpose the first two and mypy --strict has nothing to say. The lookup misses. The key resolves to
no metric — no exception, no log, no row. That is the same failure mode the two-tier lookup, the
write-time invariant check and migration 008's CHECK constraint all exist to prevent, arriving
through the one door none of them watch.

The sharpest case was `variant_kinds_per_heading: Mapping[tuple[str, str], frozenset[str]]` — three
different kinds of string in a single annotation, and `(module, column)` indistinguishable from
`(column, module)`.

## Act I — the decision that determined everything after it

The obvious move is a tagged string: `NewType("VariantKindName", str)`. It stops you passing a
column key where a kind belongs, it is free at runtime, and it needs nothing moved.

It also does not work, for the reason the whole subsystem exists:

```python
VariantKindName("PARMETER")     # typechecks. matches no catalog row. silently.
```

A misspelling is not a type error when the type is "some string". Only a real enum closes it, and
`VariantKind` is already a real enum — it just lives in `app/models/orm.py`, where the catalog has
to reach through the ORM to get at it.

So: **real enums for the closed sets, `NewType` only for the identifiers**, where the values
genuinely are open strings and only their ROLE is closed. The owner's call, in one sentence:

> Yes, move the real enum and use it in the map so that it's the real thing and not just fancy
> strings.

Measured rather than estimated, across `app/` and `scripts/`: 192 annotations mention `str`, 60 of
them carry domain meaning — 11 closed sets, 49 identifiers, 18 free text (`description`, `notes`,
`sequence`, `display_name`) that should be left alone. The other ~132 are file paths and CLI
arguments.

### Two things the plan had to correct about itself

The earlier note justified leaving `variant_kind` a `str` by asserting that `app/catalog/` must not
import the ORM. **There was no such rule.** `invariants.py` had been importing it since it was
written. The docstring in `keys.py` repeated the claim and cited the note back, which is how a made-up
constraint acquires two sources.

The same note offered `InterfaceKind` as precedent for the ORM referencing a catalog enum. It is
not: nothing in `app/models/` imports `InterfaceKind`, and the reason it lives in `app/catalog/` is
the *opposite* of `VariantKind`'s — it is there because it is **not** a database type, being a string
computed by a view at query time. The `VariantKind` move creates the ORM→catalog direction for the
first time.

Both corrections are in `type-safety-plan.md` rather than only here, because the plan is what
somebody will read next.

## Act II — the snapshot goes first

Stage 1 is not typing work at all. It is a safety net, and it comes first because without it every
stage after it is unreviewable.

The schema is code-first. There is no schema file to keep in sync, so the public contract only
exists once the code runs, and a changed annotation three layers down can alter the API with no
diff for anyone to see. A TODO in `app/graphql/schema.py` had said so for weeks.

The specific hazard is sharper than "typing might change the schema". A bare `NewType` on a
Strawberry field does **not** quietly become a scalar — it raises `TypeError` and the schema fails
to build, which is loud and self-correcting. But `strawberry.scalar()` is the natural thing to reach
for when that happens, and it *does* change the SDL, quietly. That is the shape worth catching.

Verified before pinning, because a snapshot of something unstable is a flaky test rather than a
guard: byte-identical across three processes and `PYTHONHASHSEED` 0/1/12345, builds with an
unreachable `DATABASE_URL`, passes from a different working directory. Then verified it catches
both an accidentally-added field and a `strawberry.scalar`.

Half the TODO closed. The other half — conformance to `docs/graphql-schema.md` — stays open on
purpose: that document is the design agreed before any resolver existed and still describes
`Metric`, `ScoreEntry` and `Mutation`, so the live schema is a strict SUBSET of it and asserting
equality would fail on absence rather than on error. The comment now says that instead of promising
a check nobody wrote.

## Act III — the move, and the thing that resized it

The plan sized stage 2 as "47 references across 9 files. Mechanical but wide." True, and misleading.
Counting them properly before starting:

| | |
|---|---|
| the definition, in `orm.py` | 1 |
| real uses spelled **`db.VariantKind.X`** | 43 |
| bare mentions in prose comments | 3 |

**Nothing anywhere imported it directly.** Every use went through the `db` alias. Which meant the
move had two possible diffs, not one:

* **A — move and re-export.** `orm.py` imports it (it still needs it for the column), `db.VariantKind`
  keeps resolving, all 43 sites change by zero characters. Two files.
* **B — move and re-spell.** Every site gets a direct import. Nine files.

B, and for a reason that only shows up if you look one stage ahead: stage 3 retypes `keys.py`
against the enum and will import it directly regardless. The second spelling arrives either way. A
does not avoid it — A just makes it arrive inside a commit that is about something else.

The honest caveat, recorded because it would otherwise read as a stronger guarantee than it is: the
ORM must import `VariantKind` either way, so `db.VariantKind` **still resolves** after the move.
One spelling is a convention and a grep here, not something the type system holds. No lint rule was
added, because both spellings are the same object — the cost of the wrong one is readability, not
correctness, and this project has already spent a branch learning what happens when you build
machinery to enforce a thing instead of making the wrong thing unwritable.

### What the move cost the database: nothing, and that was checked twice

The plan claimed no migration was needed because `SAEnum(VariantKind).name` derives from the CLASS
name rather than the module. Verified before the move, and again after it — the second check being
the one that counts:

```
SAEnum name:                       variantkind
class __module__:                  app.catalog.variant_kind
Metric.__table__.c...type.name:    variantkind
```

Migrations name the type by string literal (`sa.Enum(..., name="variantkind")`,
`ALTER TYPE variantkind ADD VALUE`), and `seed_catalog.py` passes a raw string through
`CAST(:variant_kind AS variantkind)`. Neither ever touches the Python class. Renaming the **class**
would rename the type; the new module's docstring says so in as many words.

### Proving the tests were actually looking

132 tests passed after the move. That is only evidence if the suite exercises the enum against
Postgres rather than merely importing it — so, per the discipline this project keeps relearning,
the fix was broken on purpose. Binding the column to `name="variantkind_renamed"`:

```
132 passed   ->   18 failed, 100 passed, 14 errors
```

Restored, green again. The green result means something now.

### A side effect worth naming

`app/catalog/invariants.py` had exactly one `db.` reference in the whole file, and it was
`db.VariantKind.INTERFACE.name`. Removing it removed the import. **`app/catalog/` no longer depends
on the ORM at all** — which is not the rule the deleted docstring invented, but is what that
docstring was groping towards. The dependency now runs one way: `orm.py` → `catalog.variant_kind`,
which must therefore stay a leaf. That constraint is real, it is new, and nothing enforces it, so it
is written in the module that has to honour it.

### The comments that went with it

Three, all of which had become false rather than merely stale:

* `keys.py`'s invented rule, plus its citation of `docs/stringly-typed-catalog-note.md` — a file
  renamed to `type-safety-plan.md` during stage 0. It was the only dangling documentation reference
  in the codebase; the other nine were checked and all resolve.
* `orm.py`'s "see `VariantKind` above", which is now an import.
* `orm.py`'s enum-section header, which read as an exhaustive list of the native Postgres ENUM
  types. It no longer is, and now says so.

## Act IV — what the `db.` prefix was actually carrying

A question about the rename, and the best one asked so far: `db.VariantKind` was partly there to say
*this type belongs to the ORM*. Is `VariantKind` now a thing that can go anywhere, with no attendant
connection to the database?

Structurally, yes — measured in a fresh process rather than argued:

```
sqlalchemy loaded:   False
app.models loaded:   False
app.database loaded: False
new app modules:     ['app', 'app.catalog', 'app.catalog.variant_kind']
MRO:                 VariantKind -> enum.Enum -> object
```

Semantically, no. `SAEnum` binds the member NAME, so the Python members and the Postgres labels are
two copies of one vocabulary, and adding a member is an `ALTER TYPE ... ADD VALUE` migration in its
own transaction. That obligation did not move. The hint did.

So the question became: what enforces the obligation now? Nothing. Measured, by adding
`VariantKind.SMUGGLED` with no migration:

```
132 passed
```

Invisible because the migrations build the type from literal strings — the test database never
consults the Python class, and a member nothing writes cannot fail. The bill arrives later, as a
`DataError` against real data.

Worth keeping in proportion: this was **not** a regression the move caused. Adding a member while
the class lived in `orm.py` was equally uncaught; `db.` was a hint, never a check. What the move did
was remove the last thing pointing at a hole that was already there — and leave a docstring section
in its place, which is the exact shape PR #14 spent a branch deleting.

### The guard, and the hole in its own first draft

`tests/test_postgres_enum_labels.py` reads `pg_enum` from the live schema and asserts the labels
equal the Python members, for every native enum the ORM binds. It needs no dev container: the
testcontainers suite already migrates a fresh database, so the types are right there.

Its first draft covered six of the seven enums. `modules.functions` is
`ARRAY(Enum(ModuleFunction))`, and a plain `isinstance(column.type, SAEnum)` walks straight past an
array's item type — so the enum with the **most members**, and therefore the most room to drift, was
the one silently uncovered. A guard that quietly stops guarding is worse than the gap it was written
for, so the collector now has its own tests rather than being trusted.

Broken on purpose, both ways, before being believed:

| what was broken | what failed |
|---|---|
| `VariantKind.SMUGGLED`, no migration | `test_the_labels_agree[variantkind]`, naming the member and the `ALTER TYPE` it needs |
| collector stops descending into ARRAY | the collector test by name, **and** the both-sides test reporting `modulefunction` as an orphan |

Order is deliberately not asserted. Postgres appends with `ADD VALUE`, so a member inserted mid-list
in Python legitimately sits last in the database; they agree today, and asserting that would turn an
ordinary future migration into a failure. A check that refuses a legitimate shape is worse than no
check — the lesson `test_catalog_invariants.py` was written to record.

132 -> 142 tests.

## Act V — the errors nobody had ever seen

Making `scripts/` a package so `mypy .` would stop halting had a second effect: it made twelve
pre-existing errors visible for the first time. CI runs `mypy app/`, so none of them had ever been
reported by anything.

Eleven were annotation gaps. **One was a real bug.** `rebuild_rosetta_stone.py` read shared-string
cells as `int(v.text)`, and `ElementTree` types `.text` as `str | None` — an empty `<v/>` is legal
xlsx and reached `int(None)`. Reproduced with a hand-built one-row workbook before touching it,
which is the only reason it can be called a bug rather than a warning:

```
old line:  TypeError: int() argument must be ... not 'NoneType'
new code:  {'aaaa…': 'Thermostability'}
```

Two of the others are worth recording for what they say about type-checking as a review tool rather
than a formality:

* `seed_recipes.seed` bound `modules` twice in one function, once to a `list[str]` and once to a
  `tuple[str, ...]`. Completely harmless at runtime, which is exactly why it survived — nothing
  except a type checker was ever going to mention it.
* `_property_of` returned `Any` out of a `str | None`, and the reason is structural: the two
  cross-script imports go through a `sys.path` insert, so `scrape_input_parameters` is not
  importable as `scripts.scrape_input_parameters`. That is not fixable by tidying, because
  `scripts/` is deliberately not installed — `[tool.setuptools.packages.find]` is `include =
  ["app*"]`. Left as it is, with the cast made explicit.

The two behavioural changes were both proved rather than assumed: the xlsx crash above, and
`migrations/env.py`'s revision hook, whose annotations must not disturb the sequential numbering
`docs`/CLAUDE.md both depend on. `alembic revision -m "probe"` still produced
`009_probe_delete_me.py` — zero-padded, sequential, correct. Probe deleted.

`mypy .` now reports 67 files and no errors, for the first time in the project's life.

## Still open

* **Should CI run `mypy .` rather than `mypy app/`?** As of `dd3345a` the whole repo type-checks
  clean — 67 files, zero errors — so the gap between what CI checks and what is actually true has
  closed on its own. Widening the job would keep it closed. Not done, because changing CI is a
  decision rather than a cleanup.
* Do the identifier aliases reach `scripts/`, or stop at the `app/` boundary? Scripts are where a
  raw string legitimately enters the system from a CSV.
* `Concept.name`, `Experiment.name`, `Module.name` are all `Mapped[str]` and all mean different
  things. One `Name` alias is useless; three is fussy.
* Whether `strawberry.scalar` for the GraphQL surface is ever worth the SDL churn. Deliberately
  unanswered until the resolvers exist and there is something to protect.

## What is left

Stage 3 (the aliases in `keys.py`, and `ScoreKey.chain` becoming the `ChainRole` that already
exists), stage 4 (`app/models/orm.py` and its `type_annotation_map` — **the stage that decides
whether the exercise was worth doing**), stage 5 (the consumers).

The test at the end is not "mypy passes". It passes today. It is whether these two stop
typechecking:

```python
metric_by_identity[(column_key, module, None, None)]        # transposed
decomposable_kinds_for(module, column_key) == {"PARMETER"}  # misspelt
```

The first is what `NewType` buys. The second is what only the real enum buys, and is why Act I went
the way it did.
