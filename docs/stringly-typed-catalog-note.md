# Strong type aliases for the catalog's strings

> **Status: open decision, not started.** Raised 2026-08-22. Nothing in the codebase depends on
> this; it is recorded here so it stops living in one person's head. Sequenced *after* the
> `spring-cleaning-in-summer` correctness items, for reasons at the bottom.

## The problem

Metric identity is four strings in a fixed order, and the type system has nothing to say about it:

```python
MetricIdentity = tuple[str, str, str | None, str | None]     # app/graphql/context.py
#                (module_name, column_key, variant_kind, variant)
```

Transpose the first two and mypy --strict is silent. The lookup misses, the key resolves to no
metric, and `ScoreEntry` reports nothing — with no exception and no log. That is precisely the
failure mode the two-tier lookup, `app/catalog/invariants.py`, and the write-time seeder check all
exist to prevent, arriving through a door none of them watch.

The same shape recurs across the subsystem:

| where | annotation | how many bare `str` |
|---|---|---|
| `context.py` | `MetricIdentity = tuple[str, str, str \| None, str \| None]` | 4, positional |
| `context.py` | `variant_kinds_per_heading: Mapping[tuple[str, str], frozenset[str]]` | 3 kinds of string in one type |
| `keys.py` | `ScoreKey(module, column_key, variant_kind, variant, chain)` | 5 |
| `invariants.py` | `Heading(module, column_key, variant_kinds, bare_rows)` | 3 |

`tuple[str, str]` as a heading key is the sharpest case: both elements are strings, both are
free-form, and nothing distinguishes `(module, column)` from `(column, module)`.

## Two different upgrades, often conflated

### (a) `NewType` for genuinely open strings

```python
ModuleName  = NewType("ModuleName", str)
ColumnKey   = NewType("ColumnKey", str)
VariantName = NewType("VariantName", str)
```

Zero runtime cost — `NewType` is the identity function, and the values stay ordinary `str` with all
their methods. mypy then rejects passing a `ColumnKey` where a `ModuleName` belongs.

The cost is explicit construction wherever a plain string enters: `ModuleName(row.module)` at every
DB read, in `decompose()`, and in test literals. That is noise, but it is noise concentrated at
exactly the boundaries where a mistake would otherwise be invisible.

### (b) Real enums for closed sets

`variant_kind` is not an open string at all — `db.VariantKind` already exists, and the catalog
stores member *names* only because Postgres enums and `decompose()` both speak strings. Likewise a
`variant` on an INTERFACE row is an `InterfaceKind` value, not free text.

This is the more valuable half and the more invasive one. It would make `DECOMPOSABLE_VARIANT_KINDS`
a `frozenset[db.VariantKind]`, and it would make several currently-writable mistakes unwritable —
including a mis-spelled kind name, which today typechecks and silently matches nothing.

## Open questions

1. **Does `VariantKindName` deserve a `NewType` at all, or should it just be `db.VariantKind`?**
   (b) subsumes it if the answer is the enum.
2. **How far does it propagate?** The ORM columns are `Mapped[str]`. Annotating those with `NewType`
   is possible but couples the model layer to the catalog vocabulary.
3. **Does `ScoreKey.chain` want one too?** It is H/L/T — a closed set with no enum yet.
4. **Where do the aliases live?** `app/catalog/keys.py` is the natural home (it already owns
   `ScoreKey`), but `context.py`'s `MetricIdentity` would then import from it — which the
   `MetricIdentity` comment already anticipates as the eventual move.

## Why this is sequenced after the correctness items

Items 1, 2 and 6 of the PR #13 cleanup list all touch the same declarations this would retype —
item 1 changes `DECOMPOSABLE_VARIANT_KINDS`'s shape outright. Doing both at once means the retyping
lands on code that is about to change, and it repeats the pattern PR #13's second review pass
punished: remediation folded into other work gets less scrutiny than the work it accompanies.

Fix the logic first, then retype the settled shapes.

## What it would have caught

Nothing so far, and that is worth being honest about — no bug in the repo's history is known to have
come from a transposed identity. The argument is prospective: the subsystem's whole failure mode is
*silent* mis-resolution, `ScoreEntry` will be built on top of these tuples, and a `tuple[str, str]`
key is the one place where the compiler could have an opinion and currently does not.
