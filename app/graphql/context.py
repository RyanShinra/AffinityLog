"""The GraphQL request context — how a resolver gets at the database.

WHAT A CONTEXT IS
-----------------
Resolvers are called by the GraphQL executor, not by the web framework, so they cannot take
FastAPI dependencies of their own. The context is the one object the framework hands the executor
at the start of a request, and it is the only channel through which per-request state (the session,
the authenticated user, the raw request) reaches a resolver. Every resolver in this app reads it as
``info.context``.

THE DECISION THIS FILE ENCODES
------------------------------
One session per HTTP request, shared by every resolver in the query tree — NOT one session per
resolver. A GraphQL query is a tree, so a single request can touch experiments, their candidates,
and those candidates' chains; giving each resolver its own session would spread one logical read
across several transactions, so a concurrent write could land between them and the response would
contain rows that never coexisted. One session is also one connection from the pool, rather than
one per node in the tree.

``app/database.py``'s ``get_session`` already provides exactly this shape, and this is the same
decision its docstring records. (``CLAUDE.md`` describes an engine-per-resolver design instead —
that note predates the session work and is stale; the code is right.)

WHY SUBCLASS ``BaseContext``
----------------------------
``strawberry.fastapi.BaseContext`` sets ``request``, ``response`` and ``background_tasks`` for you.
Nothing here needs them yet, but a plain dataclass would have to be replaced the first time
anything wants a header or a cookie, and subclassing costs nothing today.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends
from sqlalchemy import Result, Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from strawberry.fastapi import BaseContext

from app.database import get_session
from app.models import orm as db

# The catalog's natural key: (module_name, column_key, variant_kind, variant). Matches `metrics`'
# UNIQUE constraint and the first four fields of `ScoreKey`. Note `variant_kind` is the member NAME
# as a string ("INTERFACE", "PARAMETER") — that is what the Postgres enum stores and what
# `decompose()` produces, so both sides already speak it.
#
# This probably wants to live in `app/catalog/keys.py` beside `ScoreKey` once `ScoreKey.identity`
# exists and returns one. Here for now so this file stands alone.
MetricIdentity = tuple[str, str, str | None, str | None]


def metric_identity_from_db_metric(metric: db.Metric) -> MetricIdentity:
    metric_variant_kind: Final[str | None] = metric.variant_kind.name if metric.variant_kind is not None else None
    return (metric.module.name, metric.column_key, metric_variant_kind, metric.variant)


@dataclass(frozen=True)
class MetricCatalog:
    """Every catalogued metric, indexed the two ways the ScoreEntry resolver needs to ask.

    Both indexes come from ONE pass over ONE query. They answer different questions:

      * `metric_by_identity` — "what does this exact identity mean?" The 1132 lookups a full
        `{ candidates { scores } }` performs are all dict hits against this.

      * `variant_kinds_per_heading` — "along which axis, if any, do this heading's metrics vary?"
        Tier one of the two-tier lookup. Measured on the corpus: 197 of 200 keys carry their whole
        identity and hit `metric_by_identity` directly; 3 do not, and need the candidate's
        interface kind folded in first.

        The field holds EVERY axis, but only INTERFACE leaves a key incomplete — a PARAMETER
        variant is encoded in the key string and `decompose()` recovers it unaided. So the branch
        reading this asks only about INTERFACE while the field stays general.

        Asked first rather than retried on miss, because a retry reads as a general fallback when
        interface-qualification is specific to one axis. The correctness argument for asking first
        — a bare row beside INTERFACE rows would make tier one hit and never consult them — is now
        also covered by `app/catalog/invariants.py`, though that is a write-time check rather than
        a schema constraint, so it binds only rows the seeders write. See docs/graphql-schema.md,
        "Which catalog row a key means is a two-tier question".

    NAMING: both fields say what they are keyed BY, and the preposition carries meaning.
    `by` is a lookup handle — an identity tuple is a key you construct, not a thing that owns a
    metric. `per` is one entry for each domain entity. (`of` was rejected: "kinds of column" and
    "kind of candidate" both misparse — "kinds of X" is a stronger collocation in English than
    the binding we mean.) Singular/plural then follows the real cardinality rather than the
    number of keys: one metric per identity, several kinds possible per heading. The
    candidate-side map that `Context.interface_kinds()` returns is the same idea and is best
    bound as `interface_kind_per_candidate` at the point of use.

    "Heading" rather than "column": `column_key` holds the raw CSV export header, and "column"
    already means something else entirely in a database application. The pair
    (module, column_key) names a metric BEFORE disambiguation — the several catalog rows sharing
    a heading differ only by variant.
    """

    metric_by_identity: dict[MetricIdentity, db.Metric]
    variant_kinds_per_heading: dict[tuple[str, str], frozenset[str]]
    # End MetricCatalog Class


class Context(BaseContext):
    """Per-request state handed to every resolver as ``info.context``.

    Deliberately a thin holder. Anything that queries belongs in a resolver, not in here — the
    context exists to carry the session, not to become a service layer.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__()  # BaseContext populates request/response/background_tasks
        self.session: AsyncSession = session

        # `None` rather than an empty MetricCatalog: an empty catalog is a legitimate state (an
        # unseeded database), so the sentinel has to be distinguishable from the real thing or a
        # fresh install would query once per ScoreEntry forever.
        self._catalog: MetricCatalog | None = None

    async def catalog(self) -> MetricCatalog:
        """The whole metric catalog, loaded once per request.

        144 rows, and the `metrics` table is 152 KB in total — smaller than a single candidate's
        score bag. Loading it whole turns what would be 1132 round trips into 1132 dict lookups.

        Memoized on the instance, not at module level: the catalog only changes on a reseed, so a
        process-wide cache is tempting, but it buys a staleness window and an invalidation story in
        exchange for one query per request.
        """
        if self._catalog is not None:
            return self._catalog

        # WHAT `variant_kinds_per_heading` IS
        # ----------------------------------
        # It is small. Measured against the seeded corpus, 144 metric rows produce exactly FOUR
        # entries — it is not an index over the catalog, it is an exception list:
        #
        #     ('boltz2',      'complex_ipde')            -> {'INTERFACE'}
        #     ('boltz2',      'iptm')                    -> {'INTERFACE'}
        #     ('boltz2',      'protein_iptm')            -> {'INTERFACE'}
        #     ('evoprotgrad', 'pseudolikelihood_ratio')  -> {'PARAMETER'}
        #
        # The other 140 rows have a NULL variant_kind, contribute nothing, and so their
        # (module, column_key) is simply absent. A caller reads it as
        # `variant_kinds_per_heading.get(pair, frozenset())` and gets the empty set for almost
        # everything.
        #
        # THE QUESTION IT ANSWERS
        # -----------------------
        # Not "what does this key mean" — that is `metric_by_identity`. It is: *is decompose()'s
        # COMPLETE, or is it missing a piece that only the candidate knows?* Three cases, all real:
        #
        #   temstapro.clash.H
        #     decompose -> (temstapro, clash, None, None); no entry here; that identity exists in
        #     metric_by_identity as-is. One lookup, done. This is 197 of the 200 corpus keys.
        #
        #   evoprotgrad.esm_pseudolikelihood_ratio.H
        #     decompose -> (evoprotgrad, pseudolikelihood_ratio, PARAMETER, esm). It filled the
        #     variant in ITSELF, because the `esm_` prefix is right there in the key string and
        #     `_VARIANT_RULES` in app/catalog/keys.py recovers it. The PARAMETER entry above is
        #     therefore informational; nothing branches on it.
        #
        #   boltz2.protein_iptm
        #     decompose -> (boltz2, protein_iptm, None, None), and THAT IDENTITY DOES NOT EXIST.
        #     The three rows that do exist carry variant_kind=INTERFACE and a `variant` naming one
        #     of the three interface strings. The key cannot say which, because the discriminator
        #     is which chains went into the fold — a property of the CANDIDATE, not of the key.
        #
        # So the asymmetry that makes INTERFACE the only kind worth branching on is this: a
        # PARAMETER variant is encoded in the key and recoverable from the string alone; an
        # INTERFACE variant is not in the key at all. Everything else the key already tells us.
        #
        # WHY NOT LOOK UP AND RETRY ON MISS
        # ---------------------------------
        # Shorter, and it works today — the bare identity missing is currently a reliable signal.
        # But only by accident of the seeder: `scripts/seed_metric_skeleton.py` skips on
        # (module, column_key) rather than on full identity, which is what stops a variant-less row
        # existing beside the INTERFACE ones. That is a property of a script, not of the schema, so
        # the resolver should not lean on it. Asking which tier applies cannot fail that way.
        # Full reasoning: docs/graphql-schema.md, "Which catalog row a key means is a two-tier
        # question".
        #
        # WHERE THIS STOPS BEING TRUE (it is not future-proof, and should not be read as such)
        # -----------------------------------------------------------------------------------
        #   * ONE VARIANT AXIS PER METRIC. `metrics` has a single (variant_kind, variant) pair, so
        #     a column that varies along two axes at once — a PARAMETER sweep whose meaning ALSO
        #     depends on the interface — cannot be represented at all, let alone resolved. That is
        #     a schema limit, not a resolver limit.
        #   * ONLY INTERFACE IS AUTO-QUALIFIED. A future VariantKind whose discriminator also lives
        #     on the candidate would need its own arm in the lookup, and until it got one the key
        #     would resolve to no metric — silently, like every other miss here.
        #   * A VARIANT-LESS SIBLING BECOMES UNREACHABLE. If some (module, column_key) ever had both
        #     a NULL-variant row and INTERFACE rows, this always qualifies, so the NULL-variant row
        #     could never be returned. Different failure from retry-on-miss, not an absence of one.
        #     (The three interface strings themselves ARE guarded — tests/test_interface_kind.py
        #     checks the CASE arms in sql/candidate_summary.sql and migration 004 against the
        #     InterfaceKind enum, and the INTERFACE variants in seed/catalog.json against it too,
        #     all without a database. What is unguarded is the SHAPE above, not the spelling.)
        metric_by_identity: dict[MetricIdentity, db.Metric] = dict()
        kinds_seen_per_heading: defaultdict[tuple[str, str], set[str]] = defaultdict(set)

        stmt: Select[tuple[db.Metric]] = select(db.Metric).options(
            selectinload(db.Metric.module),
            selectinload(db.Metric.concept),
            selectinload(db.Metric.benchmark_results),
            selectinload(db.Metric.transform_of),
        )

        result: Result[tuple[db.Metric]] = await self.session.execute(stmt)
        rows: Sequence[db.Metric] = result.scalars().all()

        for metric in rows:
            metric_identity: MetricIdentity = metric_identity_from_db_metric(metric)
            metric_by_identity[metric_identity] = metric

            if metric.variant_kind is not None:
                kinds_seen_per_heading[(metric.module.name, metric.column_key)].add(metric.variant_kind.name)

        # Now we need to freeze the sets; recreating it is the easiest way
        # (I'm specifically not doing the dict comprehension for future readability)
        variant_kinds_per_heading: dict[tuple[str, str], frozenset[str]] = dict()

        for heading, seen_kinds in kinds_seen_per_heading.items():
            variant_kinds_per_heading[heading] = frozenset(seen_kinds)

        self._catalog = MetricCatalog(
            metric_by_identity=metric_by_identity, variant_kinds_per_heading=variant_kinds_per_heading
        )
        return self._catalog

    # End def catalog


# End Context class


async def get_context(session: Annotated[AsyncSession, Depends(get_session)]) -> Context:
    """The seam between FastAPI and Strawberry, and it is one line.

    `GraphQLRouter(schema, context_getter=...)` takes a callable, and — because `GraphQLRouter` is an
    `APIRouter` subclass — FastAPI resolves that callable's parameters with the ordinary dependency
    machinery. So the `Annotated[AsyncSession, Depends(get_session)]` parameter is filled in before
    Strawberry ever sees it. That is the whole trick: the context getter is just a
    dependency-injected function that happens to return the object resolvers will read.

    The alternative worth knowing this rejects: opening the session in the function body
    (`async with AsyncSessionLocal() as s: ...`) rather than taking it as a dependency. That runs,
    but nothing would close it — `get_session` is an async *generator* dependency, so FastAPI runs
    the teardown after the response is sent. Taking it via `Depends` is what ties the session's
    lifetime to the request's.

    Note this runs for GET /graphql too, not only for queries: the playground will not render if it
    raises.
    """
    return Context(session=session)
