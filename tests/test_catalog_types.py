"""Stage 3 of docs/scoreentry-plan.md — what a ScoreEntry will point AT.

WRITTEN BEFORE THE CODE. Expected to fail at import until `Metric`, `Module`, `Concept`,
`BenchmarkResult`, `Query.modules` and `Query.metrics` exist in `app/graphql/types.py` and
`app/graphql/schema.py`.

WHY STAGE 3 BEFORE STAGE 2, having been planned the other way round. `ScoreEntry.metric` is the
payoff of the whole chapter, and `ScoreEntry.numericValue` is specified as "null unless
`metric.valueType` is FLOAT or INT" — so stage 2 cannot be built without at least part of `Metric`.
Building the pointer before its target would mean publishing a partial `Metric` in the SDL and
growing it a commit later, which is a public contract changing twice for no reason.

IT IS ALSO CHEAPER THAN IT LOOKS. `Context._load_catalog` already issues

    select(db.Metric).options(selectinload(db.Metric.module), selectinload(db.Metric.concept),
                              selectinload(db.Metric.benchmark_results),
                              selectinload(db.Metric.transform_of))

so every object these types expose is already in memory, once per request. Stage 3 is type
declarations over data we are already fetching: no new queries, and no `MissingGreenlet` risk from
a lazy load, because nothing here lazy-loads. The one exception is `Module.metrics`, the reverse
relationship, which IS unloaded — see `TestModuleMetricsDoesNotLazyLoad`.

These tests query through `schema.execute()` rather than calling resolvers directly. That is
deliberate: the point of stage 3 is the SDL, and a resolver that returns the right object while the
schema advertises the wrong type is exactly the failure `tests/test_schema_snapshot.py` was built
to catch. Going through the executor tests both halves at once.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.graphql.context import Context
from app.graphql.schema import schema


async def _query(session: AsyncSession, document: str, **variables: Any) -> dict[str, Any]:
    """Run one GraphQL document against a session, failing loudly on any error.

    Errors are raised rather than returned because every test here asserts on data. A resolver that
    raises returns `{"data": {"field": None}}` plus an `errors` array, and asserting on the null
    without looking at `errors` is how a test reports "the field is null, as expected" about a
    crash.
    """
    result = await schema.execute(document, variable_values=variables or None, context_value=Context(session=session))
    assert result.errors is None, f"query raised: {[str(e) for e in result.errors]}"
    assert result.data is not None
    return result.data


class TestTheMetricType:
    """`Metric` — the catalog row that says what a score MEANS."""

    async def test_a_metric_carries_its_identity_and_its_meaning(self, seeded_catalog: AsyncSession) -> None:
        data = await _query(
            seeded_catalog,
            """
            { metrics { columnKey displayName valueType direction variantKind variant } }
            """,
        )

        by_display = {m["displayName"]: m for m in data["metrics"]}
        assert len(data["metrics"]) == 6, "the fixture seeds six metric rows"

        her2 = by_display["HER2 binding confidence"]
        assert her2["columnKey"] == "protein_iptm"
        assert her2["variantKind"] == "INTERFACE"
        assert her2["variant"] == "antibody-target complex"
        assert her2["valueType"] == "FLOAT"

    async def test_enums_reach_the_sdl_as_enums_not_strings(self, seeded_catalog: AsyncSession) -> None:
        """`variantKind` must be a VariantKind, not its `str()`.

        The SDL prints an enum by its member NAME, which is also what `SAEnum` binds and what
        `metrics.variant_kind` stores — so "INTERFACE" arriving here is the whole chain agreeing.
        A field typed `str` would return "VariantKind.INTERFACE" or "interface" depending on how it
        was stringified, and both look plausible in a response body.
        """
        data = await _query(seeded_catalog, "{ metrics { variantKind } }")

        seen = {m["variantKind"] for m in data["metrics"]}
        assert seen == {"INTERFACE", "PARAMETER", None}

    async def test_curated_is_computed_not_stored(self, seeded_catalog: AsyncSession) -> None:
        """`curated` has no column: it is `displayName != columnKey`.

        The fixture's three INTERFACE rows carry real display names and so are curated; its
        `temstapro.clash` row was seeded with `display_name=column_key`, which is exactly what
        `scripts/seed_metric_skeleton.py` writes for an uncurated metric.
        """
        data = await _query(seeded_catalog, "{ metrics { columnKey displayName curated } }")

        for metric in data["metrics"]:
            assert metric["curated"] == (metric["displayName"] != metric["columnKey"])

        assert any(m["curated"] for m in data["metrics"]), "the fixture has curated rows"
        assert any(not m["curated"] for m in data["metrics"]), "and at least one uncurated row"

    async def test_a_metric_reaches_its_module(self, seeded_catalog: AsyncSession) -> None:
        data = await _query(seeded_catalog, "{ metrics { columnKey module { name moduleType } } }")

        modules = {m["module"]["name"] for m in data["metrics"]}
        assert modules == {"boltz2", "evoprotgrad", "temstapro"}

    async def test_concept_is_nullable_and_populated_where_seeded(self, seeded_catalog: AsyncSession) -> None:
        """Most metrics have no concept; the fixture gives one to the interface rows."""
        data = await _query(seeded_catalog, "{ metrics { displayName concept { name label } } }")

        with_concept = [m for m in data["metrics"] if m["concept"] is not None]
        without = [m for m in data["metrics"] if m["concept"] is None]

        assert with_concept, "the fixture seeds interface_confidence"
        assert without, "and leaves others unset — the field must be nullable"
        assert with_concept[0]["concept"] == {"name": "interface_confidence", "label": "Interface confidence"}

    async def test_benchmark_results_is_empty_but_present(self, seeded_catalog: AsyncSession) -> None:
        """Honest but inert, and this test says so rather than implying coverage.

        `benchmark_results` has zero rows in the dev corpus and the tables were deliberately never
        populated (see CLAUDE.md). So this proves the wiring and the non-null list contract, and
        NOTHING about how a populated row renders. Do not read a green here as benchmark coverage.
        """
        data = await _query(seeded_catalog, "{ metrics { benchmarkResults { property n } } }")

        assert all(m["benchmarkResults"] == [] for m in data["metrics"])

    async def test_transform_of_is_not_exposed_yet(self, seeded_catalog: AsyncSession) -> None:
        """`Metric` deliberately has no `transformOf`, so asking for it must be a validation error.

        `metrics.transform_of_metric_id` is a self-FK for raw-vs-transformed metric pairs, and
        NOTHING in the repo writes it — not `seed/catalog.json`, not any seeder. Exposing it would
        also need care: `selectinload(transform_of)` loads exactly one level, so recursing
        `Metric.from_row` into the parent touches ITS unloaded `module` and raises MissingGreenlet.

        Asserted as an absence rather than left untested, because `docs/graphql-schema.md` still
        specifies the field — so the next reader will see the gap and needs to find the decision
        rather than assume an oversight. The error reaching the client uncoded is itself the point:
        a validation error is GraphQL's own, and `should_mask_error` lets it through by name.
        """
        result = await schema.execute(
            "{ metrics { transformOf { columnKey } } }", context_value=Context(session=seeded_catalog)
        )

        assert result.errors is not None
        assert "transformOf" in result.errors[0].message


class TestTheModuleType:
    async def test_modules_are_listed_with_their_type_and_functions(self, seeded_catalog: AsyncSession) -> None:
        data = await _query(seeded_catalog, "{ modules { name moduleType functions } }")

        by_name = {m["name"]: m for m in data["modules"]}
        assert set(by_name) == {"boltz2", "evoprotgrad", "temstapro"}
        assert by_name["boltz2"]["moduleType"] == "SCORE"
        assert by_name["boltz2"]["functions"] == ["BINDING_PREDICTION"]

    async def test_module_metrics_round_trip(self, seeded_catalog: AsyncSession) -> None:
        """boltz2 owns the three INTERFACE rows; evoprotgrad the two PARAMETER ones."""
        data = await _query(seeded_catalog, "{ modules { name metrics { columnKey } } }")

        counts = {m["name"]: len(m["metrics"]) for m in data["modules"]}
        assert counts == {"boltz2": 3, "evoprotgrad": 2, "temstapro": 1}


class TestModuleMetricsDoesNotLazyLoad:
    """The one relationship stage 3 exposes that is NOT already eager-loaded.

    `_load_catalog` selects Metric and eager-loads `Metric.module`; the REVERSE — `Module.metrics` —
    is untouched, so a naive `return row.metrics` lazy-loads. Under async SQLAlchemy that raises
    `MissingGreenlet` rather than quietly issuing a query, which is loud, but only if something
    actually asks for the field.

    This is the test that asks. It is separated from `TestTheModuleType` so that a failure says
    "the reverse relationship was not handled" rather than "modules are broken".
    """

    async def test_asking_every_module_for_its_metrics_issues_no_lazy_load(self, seeded_catalog: AsyncSession) -> None:
        data = await _query(
            seeded_catalog,
            "{ modules { name metrics { columnKey displayName module { name } } } }",
        )

        for module in data["modules"]:
            for metric in module["metrics"]:
                assert metric["module"]["name"] == module["name"], "the cycle must close on itself"


class TestQueryRoots:
    async def test_metrics_returns_the_whole_catalog(self, seeded_catalog: AsyncSession) -> None:
        data = await _query(seeded_catalog, "{ metrics { columnKey } }")

        assert len(data["metrics"]) == 6

    async def test_an_unseeded_database_returns_empty_lists_not_null(self, session: AsyncSession) -> None:
        """Both roots are `[T!]!` — non-null lists. Empty is a legitimate answer; null is not."""
        data = await _query(session, "{ modules { name } metrics { columnKey } }")

        assert data == {"modules": [], "metrics": []}


class TestTheSdlItself:
    """The contract, asserted directly rather than through a query.

    `tests/test_schema_snapshot.py` catches drift between the code and `schema.graphql`; this says
    what the shape must BE, so that regenerating the snapshot cannot quietly bless a wrong one.
    """

    def test_the_new_types_are_present(self) -> None:
        sdl = schema.as_str()

        for type_name in ("type Metric", "type Module", "type Concept", "type BenchmarkResult"):
            assert type_name in sdl, f"{type_name} is missing from the SDL"

    def test_the_catalog_enums_are_enums(self) -> None:
        sdl = schema.as_str()

        for enum_name in ("enum MetricValueType", "enum Direction", "enum VariantKind", "enum ModuleType"):
            assert enum_name in sdl, f"{enum_name} is missing from the SDL"

    def test_module_functions_is_an_enum_list(self) -> None:
        """A deliberate departure from the committed spec, which said `functions: [String!]!`.

        The column is `ARRAY(Enum(ModuleFunction))` — a native Postgres enum with twelve members —
        and `moduleType: ModuleType!` right beside it in the same spec is an enum. The JSON a client
        receives is identical either way, so the whole difference is in the contract: introspection
        shows the closed set, a future `modules(function:)` filter is validated before a resolver
        runs, and adding a member is already a migration that `test_postgres_enum_labels` enforces.
        `docs/graphql-schema.md` was updated to match rather than the other way round.
        """
        assert "enum ModuleFunction" in schema.as_str()
