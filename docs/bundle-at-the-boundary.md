# Bundle at the boundary — a good idea, not yet done

> **Status: agreed in principle 2026-09-06, not implemented.** Small, well-defined, and safe to do
> at any point. Logged rather than done because it was noticed mid-chapter and the ScoreEntry
> resolver was the work in hand.

## The principle

**Get the fields into a responsible object as soon as they are extracted from SQL** — at the row,
not three call sites later. Once a value leaves a query as loose parts, every function it passes
through has to keep the parts in the right order, and nothing checks that they belong together.

## Why the same five fields are not one type

`Heading`, `MetricIdentity`, `ScoreKey`, `HeadingAudit` and `MetricKey` all carry some subset of
`(module, column_key, variant_kind, variant, chain)`. They are emphatically **not** interchangeable,
and treating them as "the same fields" is how the drift started:

| type | what it means | fields |
|---|---|---|
| `Heading` | what a column is called, before disambiguation | 2 |
| `MetricIdentity` | the catalog's natural key — one row of `metrics` | 4 |
| `ScoreKey` | one raw JSONB key, decomposed — an identity *plus the chain it was measured on* | 5 |
| `MetricKey` | an identity as DERIVED FROM the corpus, plus what collapsed into it | 6 |
| `HeadingAudit` | a heading plus the shape of its catalog rows — an argument bundle for a check | 5 |

A `ScoreKey` is not a `MetricIdentity` with an extra field: `chain` is deliberately outside identity
because it says *which subject the value describes*, not *which metric it is*. That distinction is
the 200-keys-to-144-rows collapse, and it is the reason `VariantKind` has no `CHAIN` member.

`scripts/seed_metric_skeleton.py:139` has a comment doing this job by hand:

> Past this line the pair is comparable to a `MetricKey`'s identity without either side widening to
> `str` and forgetting which half is which.

That is a comment maintaining an invariant a type would carry.

**How long this has run:** `MetricIdentity` first appears 2026-08-10, `app/catalog/keys.py` on
2026-08-02. The `NewType` aliases (PR #15) fixed the *element* types; this is the remaining half —
the *bundle* types — and it is a month old rather than the fortnight it feels like.

## The census (2026-09-06)

Five places create these fields from outside data. Three already bundle; two do not.

| where | from | produces | bundled? |
|---|---|---|---|
| `app/catalog/keys.py:333,347` — `decompose()` | a raw JSONB key string | `ScoreKey` | yes |
| `app/catalog/keys.py:235,259` — `from_db_metric` | an ORM row | `Heading` / `MetricIdentity` | yes |
| `app/catalog/invariants.py:252` | SQL rows | `HeadingAudit` | yes (flat fields + `.heading`) |
| **`scripts/seed_metric_skeleton.py:144`** | SQL rows | `set[tuple[ModuleName, ColumnKey]]` | **no** |
| **`app/catalog/keys.py:108`** — `_VARIANT_RULES` | a hand-written literal | `dict[tuple[ModuleName, ColumnKey], _Rule]` | **no** |

Everything else the grep found is bundle-to-bundle (`.heading`, `.identity`, `for_interface`), which
is the shape we want.

## The sweep

1. **`_curated_column_keys` returns `set[Heading]`.** Its consumer at
   `scripts/seed_metric_skeleton.py:220` currently rebuilds the pair — `(m.module, m.column_key) not
   in curated` — and becomes `m.heading not in curated`. The hand-maintained comment above can go.

2. **`_VARIANT_RULES` is keyed by `Heading`,** and its two accessors take one:

   ```python
   decomposable_kinds_for(heading: Heading) -> frozenset[VariantKind]
   declared_variants_for(heading: Heading, variant_kind: VariantKind) -> frozenset[VariantName]
   ```

   Both are called from `HeadingAudit.problems()` as `(self.module, self.column_key)` — lines 169
   and 187 — and become `self.heading`. Note `declared_variants_for` keeps its axis argument, which
   is load-bearing: it took only the pair until 2026-08-23, and that bug let any heading whose axis
   happened to be spelled PARAMETER pass while nothing could resolve it.

3. **`MetricKey(module, column, vk, v, ...)`** at `scripts/extract_score_keys.py:113` still
   destructures an identity to rebuild it. `MetricKey(*identity, ...)` — but check
   `tests/test_extract_score_keys.py` first, which pins that mirror on purpose.

## The decision this unlocks

`HeadingAudit.heading` is a **property** today, and that is currently correct: it has one caller
(`describe()`), so a member would be ceremony.

After the sweep it has three — `describe()` plus both helper calls — and the SQL row could bundle at
construction rather than unbundling twice per `problems()`. **So do the sweep first and let the call
count decide property-versus-member**, rather than guessing at it now.

## The counter-argument, recorded

`HeadingAudit.problems()` is called exactly once per instance, which makes the class close to a
named wrapper around a pure function rather than an object with a life. The defence is the argument
bundle, not behaviour: a free `problems(module, column_key, variant_kinds, variants, bare_rows)` is
five positional arguments of which two are same-shaped tuples of different element types —
transposable, and only partly caught by mypy. `tests/test_catalog_invariants.py` constructs these by
hand with no database precisely because the classification is pure. A named class beats a free
function here; it is not evidence that every bundle needs methods.
