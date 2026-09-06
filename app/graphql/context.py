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
decision its docstring records.

That session is not handed to resolvers raw. It arrives wrapped in ``TaskSafeSession`` (also in
``app/database.py``), which owns the lock that makes sharing one session across a gathered resolver
tree safe. See that class for the measurements, and for why the lock lives with the session rather
than out here.

WHY SUBCLASS ``BaseContext``
----------------------------
``strawberry.fastapi.BaseContext`` sets ``request``, ``response`` and ``background_tasks`` for you.
Nothing here needs them yet, but a plain dataclass would have to be replaced the first time
anything wants a header or a cookie, and subclassing costs nothing today.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated

from fastapi import Depends
from graphql import GraphQLError
from sqlalchemy import Result, Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from strawberry.fastapi import BaseContext

from app.catalog.identifiers import SequenceId
from app.catalog.interface_kind import InterfaceKind
from app.catalog.keys import Heading, MetricIdentity, ScoreKey, VariantAxes
from app.catalog.variant_kind import VariantKind
from app.database import RowTuple, TaskSafeSession, get_session
from app.models import orm as db
from app.models.views import CandidateSummary


@dataclass(frozen=True, eq=False)
class MetricCatalog:
    """Every catalogued metric, indexed the two ways the ScoreEntry resolver needs to ask.

    Both indexes come from ONE pass over ONE query. They answer different questions:

      * `metric_by_identity` — "what does this exact identity mean?" The 1132 lookups a full
        `{ candidates { scores } }` performs are all dict hits against this.

      * `variant_axes_per_heading` — "along which axis, if any, do this heading's metrics vary?"
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

    IMMUTABILITY: `frozen=True` alone would be a promise this class cannot keep. It stops
    `catalog.metric_by_identity = {}` and nothing else — `catalog.metric_by_identity[k] = x`,
    `.clear()`, and mutating the values all still work, so any resolver could corrupt the memo for
    every later resolver in the same request, silently. Worse, the values are live `db.Metric`
    instances still attached to the request's session, so assigning to one would be written to
    Postgres by the next autoflush. `MappingProxyType` is what actually closes that: a read-only
    view, so the mutating call raises instead of succeeding.

    `eq=False` goes with it. `frozen=True` with the default `eq=True` synthesises a `__hash__` over
    the field tuple, so `hash(catalog)` or putting one in a set raised
    `TypeError: unhashable type: 'dict'` — an error the word "frozen" invites you to expect not to
    get. Nothing compares or hashes catalogs, and identity is the right semantics for a per-request
    memo anyway: `await ctx.catalog() is await ctx.catalog()` is what the memo test asserts.
    """

    metric_by_identity: Mapping[MetricIdentity, db.Metric]
    variant_axes_per_heading: Mapping[Heading, VariantAxes]

    def metric_for(self, score_key: ScoreKey, interface_kind: InterfaceKind | None) -> db.Metric | None:
        """The catalog row explaining this key FOR THIS CANDIDATE, or None if there is none.

        The two-tier lookup, and the only place it is written down. Tier one asks whether the key
        can identify a metric by itself; tier two supplies the piece it cannot. See the long comment
        in `_load_catalog` for why the question is asked in that order.
        """
        axes = self.variant_axes_per_heading.get(score_key.heading, VariantAxes.none())

        if not axes.interface_qualified:
            return self.metric_by_identity.get(score_key.identity)

        if interface_kind is None:
            # A `GraphQLError` with a code, not a ValueError and not an `assert`, for the reasons
            # `_load_interface_kinds` sets out: `MaskInternalErrors` would replace an uncoded
            # message with "Internal server error.", and `python -O` strips asserts. Nor `None`,
            # which would make "we cannot tell which row applies" indistinguishable from "this
            # column is not in the catalog" — the two callers-visible outcomes that must not blur.
            raise GraphQLError(
                f"score key {score_key.heading.dotted!r} is interface-qualified: which of its catalog rows "
                "applies depends on the chains this candidate folded, and no interface kind was "
                "supplied. Remedy: the candidate is missing from the candidate_summary view, which "
                "should be impossible — every candidate joins an experiment and chains are LEFT "
                "joined.",
                extensions={"code": "INTERFACE_KIND_REQUIRED"},
            )

        return self.metric_by_identity.get(MetricIdentity.for_interface(score_key.heading, interface_kind))

    # End MetricCatalog Class


class Context(BaseContext):
    """Per-request state handed to every resolver as ``info.context``.

    Still a thin holder: it carries the session and serialises access to it, and it caches the one
    thing every ScoreEntry needs. Anything that answers a domain question belongs in a resolver.

    THE TWO LOCKS, AND WHY THEY ARE TWO
    -----------------------------------
    Sharing one session across a gathered resolver tree has to be serialised. ``TaskSafeSession``
    does that and owns the lock for it; this class never sees that lock and cannot acquire it.

    ``_catalog_lock`` is a second, unrelated lock guarding a different invariant — that the catalog
    memo is built exactly once per request. It is held across the whole of ``_load_catalog()``,
    which is only safe because the statement inside that build takes the *session's* lock, a
    different object.

    That separation is the design, not an accident of refactoring. One lock serving both invariants
    is the version that deadlocks: a caller holding it across the build re-enters it on the first
    statement, and ``asyncio.Lock`` is not reentrant, so the task waits on itself — forever, with no
    exception and no traceback, while every other request on the loop is served normally. Two locks,
    one per invariant, makes that unspellable rather than merely forbidden: ``catalog()`` cannot
    misuse the session lock because it cannot reach it.

    Lock ordering, for completeness: the memo lock is always taken before the session lock and never
    the reverse. That is structural rather than a rule to remember, because the session lock is held
    only inside ``TaskSafeSession.execute``, whose critical section is one statement and calls
    nothing.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__()  # BaseContext populates request/response/background_tasks
        # Wrapped, not stored raw: resolvers get `execute_statement()` and no route to an
        # unguarded `AsyncSession`. The constructor still TAKES a plain session, so every existing
        # caller and test builds a Context exactly the way it did before.
        self._session: TaskSafeSession = TaskSafeSession(session)

        # Guards the catalog memo below, and nothing else. Emphatically not the session's lock —
        # see the class docstring for why those have to be two objects.
        self._catalog_lock = asyncio.Lock()

        # `None` rather than an empty MetricCatalog: an empty catalog is a legitimate state (an
        # unseeded database), so the sentinel has to be distinguishable from the real thing or a
        # fresh install would query once per ScoreEntry forever.
        self._catalog: MetricCatalog | None = None

        # A THIRD lock, for the same reason there is a second one. `_load_interface_kinds()` issues
        # its statement through `execute_statement()` exactly as `_load_catalog()` does, so it needs
        # a lock that is not the session's. It is also not `_catalog_lock`: the two builds never
        # nest, so sharing would not deadlock — but it would serialise two unrelated memos against
        # each other, and would put one object back in charge of two invariants, which is the shape
        # that deadlocked before. One lock per invariant is the rule; this is the third invariant.
        self._interface_kinds_lock = asyncio.Lock()

        # `None` rather than `{}`, for `_catalog`'s reason: a database with no candidates
        # legitimately produces an empty map, so the sentinel must be distinguishable from a real,
        # empty answer or an empty corpus would re-query once per ScoreEntry forever.
        self._interface_kinds: Mapping[SequenceId, InterfaceKind] | None = None

    async def execute_statement(self, statement: Select[RowTuple]) -> Result[RowTuple]:
        """Run one SQL statement against this request's session. What every resolver calls.

        Spelled longer than the `execute()` it delegates to because `Context` is a grab-bag — it
        also carries a `request`, a `response` and `background_tasks` — and because Strawberry's own
        `Schema.execute()` runs a GraphQL DOCUMENT, not a statement. This repo calls that one too
        (`test_two_root_fields_do_not_break_a_virgin_session`, in tests/test_context_catalog.py), so
        a bare `execute` would mean SQL in one file and GraphQL in another. Named rather than cited
        by line: this said `:217` and pointed six lines off within a week, because a line number in
        another file rots on any edit above it and nothing checks it.
        """
        return await self._session.execute(statement)

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

        async with self._catalog_lock:
            # Checked again inside the lock. Without this the lock would serialise the queries but
            # still run one per caller: measured, 14 concurrent callers (one per candidate in
            # `{ candidates { scores } }`, which graphql-core gathers across the list) produced 14
            # distinct MetricCatalog objects and 14 queries. The memo only works if the winner is
            # decided while the losers are waiting.
            if self._catalog is not None:
                return self._catalog
            self._catalog = await self._load_catalog()

        return self._catalog

    # End def catalog

    async def _load_catalog(self) -> MetricCatalog:
        """Build the catalog. Call only from `catalog()`, holding `_catalog_lock`.

        Split out so `catalog()` is nothing but cache policy — the double-check, the lock, the
        memo — and this is nothing but how the two indexes get built.
        """
        # WHAT `variant_axes_per_heading` IS
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
        # `variant_axes_per_heading.get(pair, frozenset())` and gets the empty set for almost
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
        kinds_seen_per_heading: defaultdict[Heading, set[VariantKind]] = defaultdict(set)

        select_db_metrics: Select[tuple[db.Metric]] = select(db.Metric).options(
            selectinload(db.Metric.module),
            selectinload(db.Metric.concept),
            selectinload(db.Metric.benchmark_results),
            selectinload(db.Metric.transform_of),
        )

        # The ordinary front door, even though `catalog()` is holding `_catalog_lock` around this
        # entire method. That is exactly what the two locks buy: this takes the SESSION's lock,
        # a different object, so there is nothing to re-enter. Under one shared lock this line
        # would hang the request forever, which is why this used to be `self.session.execute`.
        db_metrics_result: Result[tuple[db.Metric]] = await self.execute_statement(select_db_metrics)
        db_metrics_rows: Sequence[db.Metric] = db_metrics_result.scalars().all()

        for db_metric in db_metrics_rows:
            metric_identity: MetricIdentity = MetricIdentity.from_db_metric(db_metric)
            metric_by_identity[metric_identity] = db_metric

            if db_metric.variant_kind is not None:
                kinds_seen_per_heading[Heading.from_db_metric(db_metric)].add(db_metric.variant_kind)

        # Now we need to freeze the sets; recreating it is the easiest way
        # (I'm specifically not doing the dict comprehension for future readability)
        variant_axes_per_heading: dict[Heading, VariantAxes] = dict()

        for heading, seen_kinds in kinds_seen_per_heading.items():
            variant_axes_per_heading[heading] = VariantAxes(frozenset(seen_kinds))

        # Wrapped on the way out. The dicts above are mutable because building them requires it;
        # the catalog handed to 1132 resolvers must not be.
        return MetricCatalog(
            metric_by_identity=MappingProxyType(metric_by_identity),
            variant_axes_per_heading=MappingProxyType(variant_axes_per_heading),
        )

    # End def catalog

    async def interface_kinds(self) -> Mapping[SequenceId, InterfaceKind]:
        """What each candidate's ipTM-style scores are actually measuring. Loaded once per request.

        TIER TWO of the two-tier lookup. `catalog().variant_axes_per_heading` answers "is this
        heading INTERFACE-qualified?"; this answers "and what did THIS candidate fold?". Neither is
        sufficient alone, which is the whole shape of the 2026-07-31 finding: `boltz2.protein_iptm`
        is one key meaning three different physical quantities, and the discriminator lives on the
        candidate rather than in the key or the value.

        ONE QUERY FOR EVERY CANDIDATE, not one per candidate. `{ candidates { scores } }` gathers
        the score resolver across the whole list, so a per-candidate read would be 14 round trips on
        today's corpus and one per row forever after — the same argument that makes `catalog()` load
        all 144 metrics at once.

        Keyed by `candidates.sequence_id`, because that is what `candidate_summary` publishes (as
        `candidate_id`). See `_load_interface_kinds` for what that key costs and what guards it.
        """
        if self._interface_kinds is not None:
            return self._interface_kinds

        async with self._interface_kinds_lock:
            # Double-checked inside the lock, for `catalog()`'s measured reason: without this the
            # lock serialises the queries but still runs one per waiting caller.
            if self._interface_kinds is not None:
                return self._interface_kinds
            self._interface_kinds = await self._load_interface_kinds()

        return self._interface_kinds

    # End def interface_kinds

    async def _load_interface_kinds(self) -> Mapping[SequenceId, InterfaceKind]:
        """Build the interface-kind map. Call only from `interface_kinds()`, holding its lock.

        WHY THIS CAN FAIL IN THE MIDDLE OF A REQUEST, AND WHAT THAT COSTS
        ----------------------------------------------------------------
        Both failures below are NON-TRANSIENT. Neither is a blip to retry: once the data or the code
        is in the failing state, every query that touches scores fails identically until a human
        changes something. So the failure has two audiences at once — the operator, who has to go
        fix it, and the client, who needs to be told something more useful than "it broke".

        A bare `ValueError` serves neither. `MaskInternalErrors` (app/graphql/errors.py) replaces the
        message of any error NOT carrying a deliberate `code`, so the caller would receive
        "Internal server error." and have nothing to report. `GraphQLError` with a code is this
        project's existing shape for an error meant to be read — `_as_uuid` in schema.py is the
        precedent — and carrying the code is precisely what survives masking.

        The operator half is thinner than it should be, and this says so rather than implying
        otherwise: Strawberry logs the original to the `strawberry.execution` logger, and errors.py
        notes there is no aggregation to alert from yet. Today "alarm" means a line in the server
        log. If this project grows monitoring, these are two of the errors worth paging on.

        Each message therefore names its own remedy, because whoever reads it will not be holding
        this context.
        """
        select_candidates: Select[tuple[SequenceId, str]] = select(
            CandidateSummary.candidate_id, CandidateSummary.interface_kind
        )
        select_candidates_results: Result[tuple[SequenceId, str]] = await self.execute_statement(select_candidates)

        interface_kind_per_candidate: dict[SequenceId, InterfaceKind] = dict()

        for candidate_id, label in select_candidates_results.all():
            # THE KEY IS THE VENDOR'S, AND IT IS ONLY UNIQUE PER EXPERIMENT.
            # `candidate_summary.candidate_id` is `candidates.sequence_id` — the `id` column of the
            # Bio Discovery export — and `uq_candidate_seq` constrains `(experiment_id,
            # sequence_id)`, not `sequence_id` alone. This map spans all nine experiments, so two
            # colliding rows would silently fold into one entry and hand one candidate the OTHER's
            # interface kind: an antibody's heavy-light pairing confidence (~0.95) reported as HER2
            # binding, which is the precise inversion the two-tier design exists to prevent.
            #
            # No collision exists in the corpus (14 candidates, 14 distinct ids, measured), and the
            # ids look like real UUIDs — so this is belt-and-braces against a guarantee the schema
            # does not actually make, not against an observed fault.
            if candidate_id in interface_kind_per_candidate:
                raise GraphQLError(
                    f"candidate id {candidate_id!r} appears in more than one experiment, so this "
                    "request cannot tell which candidate's chains each score belongs to. Remedy: "
                    "key this map by candidates.id, which means publishing it from the "
                    "candidate_summary view (a migration).",
                    extensions={"code": "AMBIGUOUS_CANDIDATE_ID"},
                )

            # str -> enum AT THE SQL BOUNDARY, the way app/catalog/invariants.py does it, so nothing
            # downstream ever handles the raw CASE string. The enum is the shared vocabulary: the
            # same four strings are the view's CASE arms and the `variant` of the nine INTERFACE
            # catalog rows, and `InterfaceKind` is what makes them one definition instead of three.
            #
            # A label with no member means sql/candidate_summary.sql and
            # app/catalog/interface_kind.py have drifted. tests/test_interface_kind.py already
            # catches that in CI without a database, so this is the belt to that suspenders — but it
            # is raised the same deliberate way rather than left to a bare ValueError, because the
            # audience argument above does not change just because the cause is our bug.
            try:
                interface_kind_per_candidate[candidate_id] = InterfaceKind(label)
            except ValueError as exc:
                raise GraphQLError(
                    f"the candidate_summary view produced interface kind {label!r}, which is not a "
                    "member of InterfaceKind. Remedy: sql/candidate_summary.sql (and migration 004) "
                    "have drifted from app/catalog/interface_kind.py — reconcile the CASE arms.",
                    extensions={"code": "UNKNOWN_INTERFACE_KIND"},
                ) from exc

        # Wrapped on the way out, for MetricCatalog's reason: this map is handed to every ScoreEntry
        # in the request and must not be mutable by any of them.
        return MappingProxyType(interface_kind_per_candidate)

    # End def _load_interface_kinds


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
