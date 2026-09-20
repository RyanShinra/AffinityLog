# PR #15 diary — teaching the type system the vocabulary

> **Status: IN PROGRESS, updated as each stage lands.** Unlike the PR #13 and #14 diaries, this one
> is being written alongside the work rather than after it, so the later acts will be thinner than
> the earlier ones until they are done. `docs/type-safety-plan.md` is the plan and stays
> authoritative for *what* is going to happen; this file records *why it keeps changing shape*.

**Span:** 2026-08-24 → · **branch:** `type-safety` · 3 of 5 stages · 27 commits · all five stages, three review passes · 132 → 158 tests · `mypy .` clean, and CI now runs it

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

The two behavioral changes were both proved rather than assumed: the xlsx crash above, and
`migrations/env.py`'s revision hook, whose annotations must not disturb the sequential numbering
`docs`/CLAUDE.md both depend on. `alembic revision -m "probe"` still produced
`009_probe_delete_me.py` — zero-padded, sequential, correct. Probe deleted.

`mypy .` now reports 67 files and no errors, for the first time in the project's life.

## Interlude — the criterion that decides where a vocabulary lives

Stage 3 wants `ScoreKey.chain` typed as `ChainRole`, and `ChainRole` lives in the ORM. Which would
re-create the `app/catalog/` → ORM dependency stage 2 had just removed — so the question came back
one layer up, and got a better answer than the one stage 2 improvised:

> if it is truly part of the database schema (and not a free floating concept) then importing it
> from ORM is fine. The only restriction is that GraphQL can't have the literal specter of
> `db.ChainRole` as something that shows up in its SDL.

That is a sharper rule than "the catalog owns the vocabulary it reasons about", and it settles two
things at once.

**`ChainRole` stays in the ORM and gets imported as `db.ChainRole`.** It is schema: a native
`chainrole` type backing `candidate_chains.role`. Its readers are the importer, the ORM and the
GraphQL layer; the catalog would be a fourth consumer, not its home.

**`InterfaceKind` does NOT move back**, by the same rule. Measured rather than argued — the ORM
binds seven native Postgres enum types:

```
chainrole, direction, metricvaluetype, modulefunction, moduletype, provenance, variantkind
interfacekind present: False
```

There is no such type. It is a string the `CASE` in `sql/candidate_summary.sql` computes at query
time, and `views.py` types the column `Mapped[str]`. Its own docstring said so all along: putting it
beside the native enums "would imply an `ALTER TYPE` obligation that does not exist". Its *values*
are stored — they are the `variant` column of the nine INTERFACE metric rows — but that is data, not
schema. The two enums land on opposite sides of the line, which is why they were already in
different places.

**The SDL restriction is already satisfied, and now provable.** The committed snapshot says
`enum ChainRole` while the class is reached as `db.ChainRole`. Strawberry names types from
`__name__`, not the module — verified with two identical enums in differently-named packages:

```
pkg_alpha  __module__=pkg_alpha  -> SDL: enum Widget {
pkg_beta   __module__=pkg_beta   -> SDL: enum Widget {
```

The same rule as `SAEnum`, arrived at twice on this branch for two different reasons. A Python
spelling cannot reach the contract, and stage 1's snapshot fails if that ever changes.

### Three "for now" comments, and which gates have opened

An audit prompted by a half-remembered note about something living somewhere temporarily:

| comment | gate | open? |
|---|---|---|
| `context.py:58` — `MetricIdentity` "probably wants to live in `keys.py`… once `ScoreKey.identity` exists" | `ScoreKey.identity` | **yes, in stage 3** |
| `test_context_catalog.py:33` — `_identity_for` is in the test "because the resolver does not exist yet" | the `ScoreEntry` resolver | no — the home stretch |
| `orm.py:451` — `Artifact`, "the placeholder hook for now" | artifacts being served | no |

The middle one is the sharpest and worth naming before it is due: that test currently guards a
*copy* of the two-tier lookup rather than the lookup itself. Its own docstring says to delete it
when the resolver lands, "or the test stops guarding the real code path".

## Act VI — stage 3, in four parts

Split four ways because a 60-site retype landing as one diff is unreviewable, and each part turned
out to answer a different question.

**3a — the chain suffix.** `_CHAIN_SUFFIX` was `r"\.(H|L|T)$"` and `ChainRole` was
HEAVY="H"/LIGHT="L"/TARGET="T", with nothing checking the two agreed. The pattern is built from the
enum now — the same string today, checked, and derived from here on. Proved it rather than assuming
the equality was causal: adding `SCAFFOLD = "S"` gives `\.(H|L|S|T)$` with nothing else edited.

The nice part was unplanned. That same probe also turns
`test_the_labels_agree[chainrole]` red, because a new role owes an `ALTER TYPE`. Widening the regex
and owing a migration are both automatic now, and neither was arranged — they just both follow from
the enum being the source.

**3b — the move.** `MetricIdentity` to `keys.py`, and `ScoreKey.identity` to go with it, closing the
comment whose gate had finally opened. Three call sites were rebuilding the four-tuple by hand.

mypy caught what stage 2 had to settle with a convention. With the alias moved, one test was still
importing it from `app.graphql.context`, and strict mode refuses an implicit re-export **by name**:

```
Module "app.graphql.context" does not explicitly export attribute "MetricIdentity"
```

Stage 2's "one spelling is a convention and a grep, not something the type system holds" was true of
an enum the ORM must import either way. It is not true here.

**3c — the enum.** `variant_kind` stopped being a member name carried five layers above the one
place it comes from. The string boundary is now two marked lines: `VariantKind[label]` where
`_HEADINGS_SQL`'s `::text` comes back, and `.name` where `seed_metric_skeleton` binds
`CAST(:variant_kind AS variantkind)`.

**That second line was a bug I introduced, and mypy did not catch it** — the bind parameters go
through an untyped dict. Found by reading, then reproduced against the dev database rather than
argued:

```
bind VariantKind.PARAMETER        -> asyncpg DataError
bind VariantKind.PARAMETER.name   -> 'PARAMETER'
```

Worth recording as the honest limit of the whole exercise: **raw SQL is a hole the type system does
not cover.** Every remaining `text()` bind is a place a retyped value can still go wrong silently,
and there is no stage of this plan that closes them.

One test had to be rewritten rather than fixed. `test_variant_kinds_are_member_names_not_values`
guarded against `.value` ("interface") being used where `.name` ("INTERFACE") was meant. Tier one
holds members now, so that mistake is unspellable *there* — but the hazard did not vanish, it moved
to the two lines above. The test says so instead of pretending it is gone.

**3d — the identifiers.** `ModuleName`, `ColumnKey`, `VariantName`. Both failing lines from the plan
now fail to typecheck, and the correct spellings still pass:

```
metric_by_identity[(column_key, module, None, None)]
  -> Invalid index type "tuple[ColumnKey, ModuleName, None, None]"
decomposable_kinds_for(m, c) == {"PARMETER"}
  -> Non-overlapping equality check
```

They are deliberately **not** a spelling check — `ModuleName("bolz2")` is a perfectly good
ModuleName that matches nothing. That is the whole reason the closed sets went the other way in 3c.

### The number that decides stage 4

App code needed five fixes. **Forty-nine of the fifty-four errors were test literals.** Rather than
wrap thirty of them inline, each test file gained one helper taking plain strings — safe there
because the expected value is compared against real output, so a transposed argument fails the
assertion loudly, where in production it would miss the catalog and resolve to nothing.

But five of those app fixes are casts that should not exist. `metric_identity_from_db_metric` now
re-wraps three fields to say what they already are, because `Module.name`, `Metric.column_key` and
`Metric.variant` are still `Mapped[str]`:

```python
return (
    ModuleName(metric.module.name),
    ColumnKey(metric.column_key),
    metric.variant_kind,          # already a VariantKind — no cast
    VariantName(metric.variant) if metric.variant is not None else None,
)
```

The one field that needs no cast is the one whose ORM type is already right. **That is stage 4's
argument, arrived at by writing the code rather than by predicting it** — and the comment beside
those casts says they are stage 4's to delete.

## Act VII — what the review found

Three findings, none of them in the typing itself. All three were in the *seams* the branch moved.

**The sort that data can crash.** `extract_score_keys` ends with `sorted(buckets.items())`, which
compares identity tuples element-wise — and since 3c the third element is `VariantKind | None`,
neither of which defines `__lt__`. Two keys under one heading is enough:

```
evoprotgrad.pseudolikelihood_ratio        -> (…, None,      None)
evoprotgrad.esm_pseudolikelihood_ratio    -> (…, PARAMETER, 'esm')
TypeError: '<' not supported between 'NoneType' and 'VariantKind'
```

Reachable from **data**, not from a code change — one export that stops prefixing the model name.
The `None`/`str` form of it predates the branch; retyping the tuple widened it to enum-vs-enum too.
Worth noticing what this says about the exercise: mypy accepted the sort at every stage, because
`sorted` is happy to take anything and the failure is at runtime.

**The cycle that was one line away.** `orm.py` imports `app.catalog.variant_kind`, which runs
`app/catalog/__init__.py`; `keys.py` imports the ORM back for `ChainRole`. So after 3a, a single
convenience re-export in that `__init__.py` would take the whole application down at import with an
`AttributeError` about `ChainRole` raised from `keys.py` — nowhere near the line at fault.

The property was *already* load-bearing before this branch (it is why moving `VariantKind` worked at
all), and it was recorded only in the plan document. That is the recurring lesson of PR #14 arriving
in a new costume: a constraint that lives somewhere other than the file that has to honour it is a
constraint waiting to be broken. It is now in the docstring, with the cycle drawn out, and
`tests/test_catalog_package_has_no_imports.py` parses the file with `ast` and fails if an import
appears — cheap, needs no database, and reports the line number instead of leaving you to debug the
AttributeError.

**The half-fixed package.** `dddc8f9` added `scripts/__init__.py` to stop `mypy .` halting, but left
two cross-script imports going through a `sys.path.insert` of `scripts/` itself. One file could then
load under both names, as two module objects with two copies of every class — `top.RecipeDag is
pkg.RecipeDag` was False. Fixing the symptom and leaving the cause is exactly what that commit
should not have done.

Both now insert the repo root and import `scripts.<name>`. **The fix paid for itself in one step:**
with the import resolvable, mypy could finally see `scrape()`'s signature and reported that
`_property_of` iterates an `object`. `scrape()` gained a `ScrapedPage` TypedDict, and the `str(...)`
wrapper added back in `dd3345a` — which existed only to launder an unresolvable import — deleted
itself.

Which is the review's real finding, stated once: **the typing work was sound; the seams around it
were not.** Every defect was at a boundary the branch moved — a sort, a package `__init__`, an
import path — and none was in the retyped code itself.

## Act VIII — stage 4, and a correction about how it was reached

The stage that was supposed to decide whether any of this was worth doing. It was, and it cost
almost nothing: three annotations, and the casts deleted themselves.

```python
# before                                  # after
ModuleName(metric.module.name),           metric.module.name,
ColumnKey(metric.column_key),             metric.column_key,
metric.variant_kind,                      metric.variant_kind,
VariantName(metric.variant) if ...,       metric.variant,
```

**The plan was wrong about two things**, both measured. Stage 4 as written was *impossible* —
`orm.py` importing the aliases from `keys.py` closes a cycle, because `keys.py` imports the ORM back
for `ChainRole`. And no `type_annotation_map` entries were needed: `Mapped[NewType]` is deprecated
only on a BARE annotation, where it also silently drops the length (`VARCHAR`, not `VARCHAR(128)`).
Every column here is explicit.

### The correction, which is the part worth keeping

The fix shipped was a new leaf module, `app/catalog/identifiers.py`. Then the owner asked the
question that should have been asked first:

> the correct solution is to identify the place where the type is used only as a type annotation and
> not actually as a solid object. Find that and use the import guards to break the cycle. Is that
> what you did?

No. And the honest failure is not the answer — it is that **the alternative was never evaluated.**
The leaf module was reached by pattern-matching on stage 2, which had the same shape, rather than by
eliminating the cheaper option. Those look identical from the outside when the conclusion happens to
agree; they are not the same, and only one of them survives the conclusion being different.

Tested afterwards, which is the wrong order but better than never. Neither edge is annotation-only:

| edge | guardable? | why |
|---|---|---|
| `orm.py` → the aliases | **no** | SQLAlchemy resolves `Mapped[...]` at class creation — `MappedAnnotationError`, and `from __future__ import annotations` does not rescue it |
| `keys.py` → `ChainRole` | **no** | `_CHAIN_SUFFIX` iterates the enum at IMPORT time; `decompose` calls `ChainRole(...)` per key |

The `ScoreKey.chain` annotation *would* have been guardable — a `NamedTuple` stores it as a
`ForwardRef` and never resolves it — but it is one of three uses. With no annotation-only edge, the
leaf is what is left.

That reasoning now lives in `identifiers.py`'s docstring, because "why not just `TYPE_CHECKING`?" is
the first thing a competent reader asks, and a file that cannot answer it invites the change that
breaks it.

### What stage 4 does not buy

Checked rather than assumed. Reads and attribute assignment are protected; **constructor kwargs are
not**, because SQLAlchemy's declarative `__init__` is `**kw: Any`:

```
m.column_key = "protein_iptm"     -> error
db.Module(name="boltz2", ...)     -> accepted
```

Which is the third entry in a growing list — raw SQL binds (3c), `sorted()` (the review), and now
ORM construction. The pattern across all three: **mypy protects the code it can see the types of,
and every one of these holes is a place where a value crosses into something dynamically typed.**
That is worth knowing before anyone reads the aliases as airtight.

### The guard that replaced the guard

`tests/test_import_graph.py` supersedes the narrow `__init__.py` check from Act VII. It asserts that
every module the ORM can reach imports nothing from `app`, and — in a subprocess, because
`sys.modules` is already populated by the time pytest runs — that the three entry points each import
first on a cold interpreter. Probing both shapes showed they are complementary rather than
redundant: an import in `identifiers.py` trips only the static check, because it binds the module
without using it, while one in `__init__.py` trips both.

Writing it also caught the first version forbidding ALL imports, which would have forbidden the leaf
files themselves — `enum` and `typing` are what they are made of. Stdlib is not the hazard; a cycle
needs two of our own modules.

## Act IX — the second review, which found nothing in the code

Three findings, all three in `tests/test_import_graph.py` — the guard written one commit earlier to
prevent this exact class of bug. Stage 4's retype came through clean.

**Relative imports were invisible.** The leaf check tested `node.module.startswith("app")`, and
`from . import keys` has `module=None` while `from ..models import orm` has `module="models"`.
Neither matches. Worse, a relative import that is only BOUND evaded both checks at once — the static
one on the name, the dynamic one because nothing used it at import time. That is precisely the
loaded-but-not-fired state the static check was added for, so the guard had a hole shaped like its
own justification.

**Two of the three were the same mistake twice: enumerating by hand what should have been derived.**
`_LEAVES` was a hardcoded tuple, though the set that must be leaves is whatever `app/models/`
imports from `app.catalog`. `_ENTRY_POINTS` named `app.graphql.schema` as "what the server builds",
but the server imports `app.main` — and `app.models.views` plus all three routers were reached by
none of the three entry points. Measured: a firing cycle between `views.py` and `routers/demo.py`
leaves both other entry points importing fine.

Both fail by SILENCE. Green test, unenforced constraint. Which is the thing
`test_postgres_enum_labels.py` had already grown collector tests to avoid, one act earlier in this
same diary — the lesson was written down and then not applied to the next guard. Both sets are
derived now, and `test_the_derivations_found_something` is the collector test applied properly.

### Two probes that lied

Worth recording, because both looked like passing evidence:

* The first probe for the `_LEAVES` fix introduced a cycle so severe it broke pytest **collection** —
  conftest imports the ORM — so `grep FAILED` found nothing and the fix appeared not to work. It
  did; the probe was measuring an empty output.
* The first probe for the `app.main` fix imported a module without using it, so nothing fired and
  every entry point passed. Rebuilt to import a NAME rather than a module, it failed exactly one
  test — the right one.

Neither was evidence of anything until it was rebuilt. A probe that produces the expected output for
the wrong reason is worse than no probe, because it ends the investigation.

## Act X — the stage I reported as done, and had not done

Updating the plan after stage 4, I marked stage 5 **"DONE, as fallout"** — mypy had named every
consumer as each earlier stage broke it, `mypy .` was clean, and there seemed to be nothing left.
The owner's question was two words long and correct:

> Wait, when did we do step 4? Let alone, step 5?

Stage 4 was real. Stage 5 was not, and the mechanism I missed is worth more than the fix.

**mypy reports a WIDE value arriving in a NARROW slot. The opposite direction is legal and silent.**

```python
def narrowing_is_silent(m: ModuleName) -> tuple[str, ...]:
    return (m,)          # Success: no issues found — the ModuleName is gone
```

`ModuleName` *is* a `str`, so a `str` slot accepts one and simply forgets it. Nothing is wrong
enough to report. Almost the whole of stage 5 is that shape — which is exactly why the plan gave the
consumers their own stage rather than trusting the compiler, a sentence I had written and then
argued myself out of by reading a green `mypy .` as an empty worklist.

**The drift the plan warned about had already shipped.** `MetricKey` is documented as mirroring
`ScoreKey`. Three of its four identity fields had been bare `str` since stage 3:

| field | `ScoreKey` | `MetricKey`, before |
|---|---|---|
| `module` | `ModuleName` | `str` |
| `column_key` | `ColumnKey` | `str` |
| `variant_kind` | `VariantKind \| None` | same |
| `variant` | `VariantName \| None` | `str \| None` |

### The answer to a question the plan had been carrying

*"Do the identifier aliases reach `scripts/`, or stop at the `app/` boundary?"* — recorded as open,
then ignored, which is the real reason stage 5 could not have happened by accident. The answer is
**by role, not by directory**: a script type that MIRRORS an app-side type follows it, and local
seeder plumbing stays `str` because that is where a raw name enters from a CSV header — the boundary
the aliases exist to have. Both seeder signatures now say so, since a reader who has seen
`ModuleName` everywhere else would otherwise read a bare `str` as an oversight.

### The half that was actually missing

Not the retype — the enforcement. `tests/test_extract_score_keys.py` compares *resolved* type hints
(`get_type_hints`, not `__annotations__`: both modules use postponed annotations, so the raw values
are strings that would compare equal on spelling alone). Reverting `MetricKey` to bare `str` — the
exact drift that shipped — produces:

```
mypy .   ->  Success: no issues found in 70 source files
pytest   ->  3 failed
```

That contrast is the whole lesson of the act, in two lines.

### And the sha that pointed at nothing

The stage 5 commit cited its own hash in the plan; an `--amend` immediately after moved it, leaving
a reference to a commit that never existed on the branch. Caught by checking every hash the document
cites against `git cat-file`, which is now the obvious thing to have been doing all along. Fixed
forward rather than by amending again, which would have moved it a third time.

## Act XI — the same mistake a third time

A question after the branch was declared finished: *are the code review lines sufficiently covered?*

All six findings from the two passes verified fixed in the live tree. But the audit turned up
something the checklist could not: **stage 5 shipped after the second review pass, so it had never
been reviewed at all.** Reviewing it found the mistake from Act IX, again.

`tests/test_extract_score_keys.py` pinned the ScoreKey/MetricKey mirror against a hand-written
`_MIRRORED = ("module", "column_key", "variant_kind", "variant")`, and `_fields[:4] == _MIRRORED`
cannot see past index 4. Simulated before touching anything:

```
_fields[:4] == _MIRRORED   -> True for both
per-field checks           -> all pass
the fifth field            -> ScoreKey=int, MetricKey=str, UNCHECKED
```

`MetricIdentity` defines the arity, so it decides now. The guard-the-guard test also got stronger
rather than merely adapted: it asserts `ScoreKey.identity` actually RETURNS that many values, tying
the arity to behavior instead of to a second annotation that could drift from the first.

**Three times is a pattern, not an accident.** `_LEAVES`, `_ENTRY_POINTS`, `_MIRRORED` — each one a
literal list standing in for something the code already knew, each failing by silence rather than by
a red test. The tell is identical every time: a constant whose correct value is derivable from
something else in the repository. That belongs in CLAUDE.md, not in a diary nobody greps.

The second lesson is procedural and duller: **the last commit before a review is not the last commit
in the branch.** Two review passes had run, six findings were fixed, and everything after the second
pass went out unexamined — including the only code commit among them.

## Act XII — chasing it to the edges, and re-committing a bug I had just fixed

A third review pass, scoped to what shipped after the second one. Two findings, both the same
species: **a comment asserting something the code does not do.**

The first was the branch's third invented justification, after `keys.py`'s "app/catalog must not
import the ORM" and stage 4's two wrong plan facts. It said `name: str` was right because "this is
where a raw name ENTERS the system from a CSV header". Grepping both seeders for "csv" returned
exactly one hit each — the comment itself. `_register_module` is called only with `m.module` off a
`MetricKey`, already through `decompose()`.

The owner's steer settled what to do about it, and it is worth keeping verbatim:

> In general, more type annotations, not fewer. [...] I realize it's "all `str` at some point", much
> like how we're all naked under our clothes, but we want to chase that as far to the edges of the
> project as we can.

So the annotations followed the data rather than the comment being reworded, and the casts moved to
where the values actually arrive — the SQL row, the same shape `invariants.py` already uses.
`(m.module, m.column_key) not in curated` now compares two halves the type system can tell apart.

### The part that stings

Chasing it outward turned up `infer_value_type` returning bare `"CATEGORICAL"`/`"BOOL"` strings —
MetricValueType member names, the exact `"PARMETER"` hazard 3c closed for `variant_kind`. Making it
return the enum was right. It also **re-created the first review's bug, two hours after fixing it**:

```
sorted(tally.items())   ->  TypeError: '<' not supported between instances of 'MetricValueType'
f"{t}={n}"              ->  "MetricValueType.FLOAT=12", not "FLOAT=12"
```

Invisible against this corpus, which is 100% curated, so the tally is empty and the line prints
nothing. **Found by noticing the blank output, not by any test** — the dry run reported
`inferred types:` followed by nothing, which is what an empty dict and a crash-in-waiting look like
from the outside.

Two lessons, and the second is the useful one. Converting a string to an enum is never a local
change: every `sorted`, every f-string, every dict key downstream is a site the type checker will
not flag. And a line of output that is *empty* on the real corpus is not a line that has been
tested — it is one that has never run.

### The self-refuting docstring

The second finding was smaller and sharper: a docstring arguing "mypy cannot catch this", with a
worked example labeled `# Success: no issues found` that had become an error. It was accurate about
the bare-`str` `MetricKey` it described, and stopped being the moment stage 5 fixed that —
`NamedTuple.__init__` IS typed. The claim survives; the demonstration did not. Replaced in all three
places it had been copied, with the file now saying why the old one was wrong rather than quietly
dropping it.

## Still open

* ~~Should CI run `mypy .`?~~ **Done.** The narrow scope existed because the rest of the tree had
  errors nobody had triaged; once they were cleared it only bought a gap between what CI checks and
  what is true.
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
decomposable_kinds_for(module, column_key) == {"PARMETER"}  # misspelled
```

The first is what `NewType` buys. The second is what only the real enum buys, and is why Act I went
the way it did.
