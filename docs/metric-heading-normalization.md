# Candidate migration: lift `variant_kind` to the heading

**Status: open, deliberately not done.** The invariant below is enforced by a check at write time
(`app/catalog/invariants.py`, called by both catalog seeders). This note records the structural fix
so the choice to defer it is visible rather than accidental.

## The invariant, and where it comes from

`metrics` is unique on `(module_id, column_key, variant_kind, variant)` with `NULLS NOT DISTINCT`.
That is the right key for a *row*, but the `ScoreEntry` lookup relies on something stronger that no
constraint states — a **functional dependency**:

```
(module_id, column_key)  ->  variant_kind
```

In words: every catalog row sharing a heading must be disambiguated along the *same* axis. The
two-tier lookup in `app/graphql/context.py` asks "is this heading INTERFACE-qualified?" and then
qualifies the identity accordingly. That question only has an answer if the heading has exactly one
axis.

The database currently accepts both of these, because they differ in `variant_kind` and so do not
collide:

```
(boltz2, foo, PARAMETER, 'esm')
(boltz2, foo, INTERFACE, 'antibody-target complex')
```

## Why no lookup can rescue that state

It is tempting to read a two-axis heading as "the resolver needs a smarter branch". It is not.
A metric row carries **one** `(variant_kind, variant)` pair, so there is no row for *"esm **and**
complex"* — the cross product has nowhere to live. A heading catalogued along two axes is already
incoherent at rest; the resolver is just where you'd notice.

Concretely, with the branch as designed: `decompose()` recovers `PARAMETER='esm'` from the key
string, the resolver sees `INTERFACE` among the heading's kinds, rebuilds the identity around the
candidate's interface kind, and the `esm` is silently discarded.

## The structural fix

Postgres cannot express a functional dependency with `UNIQUE` or `CHECK` — a `CHECK` may not contain
a subquery, and the dependency is inherently about *other rows*. It can express it with **structure**,
by making the axis a property of the heading rather than of the row:

```sql
CREATE TABLE metric_headings (
    module_id   uuid         NOT NULL REFERENCES modules (id) ON DELETE CASCADE,
    column_key  varchar(128) NOT NULL,
    variant_kind variantkind,                -- NULL = this heading has exactly one metric
    PRIMARY KEY (module_id, column_key),     -- <- one axis per heading, by construction
    UNIQUE      (module_id, column_key, variant_kind)   -- FK target below
);

ALTER TABLE metrics
    ADD FOREIGN KEY (module_id, column_key, variant_kind)
        REFERENCES metric_headings (module_id, column_key, variant_kind);
```

The parent's primary key makes a second axis for one heading **unrepresentable**, and the composite
foreign key forces every metric to agree with its heading. `metrics` then keeps only `variant`,
which is the honest split: the axis describes the heading, the value describes the row.

It also makes tier one of the lookup a single indexed read against `metric_headings` rather than an
aggregate built in Python — `variant_kinds_per_heading` would collapse to a plain
`variant_kind_per_heading`, singular, because the schema would guarantee it.

## Why it is deferred

- 144 metric rows, one operator, and no violation has ever occurred — the seeded catalog is clean.
- It lands in the middle of the ScoreEntry chapter, and a schema change to close a hole nothing has
  hit is the wrong thing to interrupt that with.
- The write-time check gets most of the benefit for a fraction of the cost: it fails when the bad
  row is written, names the heading, and rolls the seed back.

## What the check does NOT cover

- Rows written by anything other than the two seeders — a manual `INSERT` in TablePlus, say.
- The second violation it also detects (a variant-less row orphaned beside **INTERFACE** rows) is a
  *reachability* bug rather than a coherence one: the bare row is dead data that looks live.
  INTERFACE specifically — only that axis makes tier one qualify, so only that axis can strand a
  bare row. A bare row beside a TRANSFORM row, which is the shape `seed/catalog.json` names as the
  next catalog addition, resolves perfectly well and is not a violation. The
  normalization above does not fix that on its own; it would need `variant` itself to be
  `NOT NULL` whenever the heading declares an axis.
- A third check now also refuses a heading qualified along an axis nothing can supply — no bare row,
  no INTERFACE, and a kind `decompose()` cannot read from the key string. That one is not about the
  functional dependency at all; it guards the *other* half, that the axis a heading declares is one
  the lookup can actually satisfy. The normalization would not fix it either: `metric_headings`
  would still happily declare `TRANSFORM` for a heading no key can resolve.

## Related

- `app/catalog/invariants.py` — the check, and why it is not in the resolver
- `app/graphql/context.py` — the two-tier lookup and its documented limits
- `docs/graphql-schema.md`, "Which catalog row a key means is a two-tier question"
- `docs/recipe-topology-note.md` — the other deliberately deferred schema decision
