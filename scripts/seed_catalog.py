#!/usr/bin/env python3
"""Load seed/catalog.json into the concepts / modules / metrics tables. Idempotent.

WHAT THIS IS
------------
The catalog is *reference data*, not experiment data: what each score key MEANS. It is hand-curated
(see seed/catalog.json) and re-seeded whenever that file changes, so this script must be safe to run
any number of times — never duplicating a row, always converging on what the JSON says.

HOW IDEMPOTENCE WORKS
---------------------
Postgres `INSERT ... ON CONFLICT (natural key) DO UPDATE`. Each table has a natural key that is
stable across re-seeds, so a second run UPDATEs the row it inserted the first time:

    concepts   -> name
    modules    -> name
    metrics    -> uq_metric_identity (module_id, column_key, variant_kind, variant)

The metrics constraint is declared `UNIQUE NULLS NOT DISTINCT`, and that is doing real work here.
Most metrics have NO variant, so variant_kind and variant are NULL. Under Postgres's default rule
NULL != NULL, which means two identical variant-less rows would NOT collide — every re-run would
insert another duplicate. NULLS NOT DISTINCT makes those NULLs compare equal, so ON CONFLICT
actually matches and the re-run updates instead. Without it this script would silently multiply
rows on every run.

FK RESOLUTION
-------------
The JSON refers to parents by NAME ("concept": "humanness"), because a hand-edited file cannot
contain UUIDs. So we build the graph bottom-up in dependency order, keeping a name -> id map from
each pass and using it to resolve the next:

    concepts  -> {name: id}
    modules   -> {name: id}
    metrics   (resolves module_id and concept_id from those two maps)

`RETURNING id` on the upsert is what gives us the id whether the row was inserted or updated.

    python scripts/seed_catalog.py            # seed / re-seed
    python scripts/seed_catalog.py --dry-run  # parse, report, and predict the real run

`--dry-run` does not touch nothing. It connects, applies the same upserts the real run would, asks
the heading-invariant check what it thinks, and then returns WITHOUT committing, so the transaction
is discarded. That is the only way the preview can predict an abort: the violation that actually
bites is a curated row landing beside one `seed_metric_skeleton` already committed, which cannot be
seen from the JSON alone. So it needs a reachable database and write permission — it just never
keeps anything. A database it cannot use is reported, not fatal.
"""

from __future__ import annotations

import argparse
import asyncio
import enum
import json
from pathlib import Path
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.invariants import find_heading_violations, raise_on_heading_violations
from app.database import AsyncSessionLocal


class Exit(enum.IntEnum):
    """What this script's exit status means.

    MIRRORS THE REAL RUN, THEN REFINES IT. Measured: the real run exits 0 when it commits and 1
    when anything stops it — an unmigrated database, an unreachable one, a heading violation — all
    of which reach the shell as an uncaught exception. So `--dry-run` returning 0 for "would
    succeed" and non-zero for "would not" is not a separate contract, it is a PREDICTION of the
    real run's status, which is the only useful thing a preview can promise.

    The split below refines that prediction without breaking it. Every non-zero still means "the
    real run would fail", so `--dry-run && seed_catalog.py` behaves identically; callers that want
    to know WHICH failure can look, and the two want different things done about them:

        CATALOG_INVALID    the JSON is wrong. Edit seed/catalog.json. Re-running will not help.
        COULD_NOT_VERIFY   the database could not be reached or used. Start it, migrate it, and
                           re-run — the catalog itself may be perfectly fine, and half the checks
                           (parse, referential integrity) did pass.

    2 IS DELIBERATELY SKIPPED. argparse exits 2 on a usage error, before any of this code runs
    (verified: `seed_catalog.py --nonsense` -> 2). Reusing it would make "you typed the flag wrong"
    and "your catalog is broken" indistinguishable.

    When both apply — a broken catalog AND an unusable database — CATALOG_INVALID wins, because it
    is the one that is certainly true and certainly needs fixing.
    """

    CLEAN = 0
    CATALOG_INVALID = 1
    COULD_NOT_VERIFY = 3


_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
_CATALOG: Final[Path] = _REPO_ROOT / "seed" / "catalog.json"


async def _upsert_concepts(session: AsyncSession, rows: list[dict[str, Any]]) -> dict[str, str]:
    """Upsert concepts, returning {name: id} for the metrics pass."""
    ids: dict[str, str] = {}
    for row in rows:
        result = await session.execute(
            text("""
                INSERT INTO concepts (id, name, label, description)
                VALUES (gen_random_uuid(), :name, :label, :description)
                ON CONFLICT (name) DO UPDATE
                    SET label = EXCLUDED.label,
                        description = EXCLUDED.description
                RETURNING id
                """),
            {"name": row["name"], "label": row["label"], "description": row.get("description")},
        )
        ids[row["name"]] = str(result.scalar_one())
    return ids


async def _upsert_modules(session: AsyncSession, rows: list[dict[str, Any]]) -> dict[str, str]:
    """Upsert modules, returning {name: id} for the metrics pass."""
    ids: dict[str, str] = {}
    for row in rows:
        result = await session.execute(
            text("""
                INSERT INTO modules (id, name, module_type, functions, description)
                VALUES (
                    gen_random_uuid(),
                    :name,
                    CAST(:module_type AS moduletype),
                    CAST(:functions AS modulefunction[]),
                    :description
                )
                ON CONFLICT (name) DO UPDATE
                    SET module_type = EXCLUDED.module_type,
                        functions   = EXCLUDED.functions,
                        description = EXCLUDED.description
                RETURNING id
                """),
            {
                "name": row["name"],
                "module_type": row["module_type"],
                # A real Python list, NOT a '{A,B}' array literal. asyncpg encodes arrays itself via
                # prepared-statement binds and rejects the string form that psycopg2 would accept
                # ("a sized iterable container expected"). The CAST in the SQL supplies the element
                # type; the driver supplies the elements.
                "functions": row.get("functions", []),
                "description": row.get("description"),
            },
        )
        ids[row["name"]] = str(result.scalar_one())
    return ids


async def _upsert_metrics(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    module_ids: dict[str, str],
    concept_ids: dict[str, str],
) -> int:
    """Upsert metrics, resolving module/concept names to ids from the earlier passes."""
    for row in rows:
        module_name = row["module"]
        if module_name not in module_ids:
            raise KeyError(f"metric {module_name}.{row['column_key']} names an unlisted module")
        concept_name = row.get("concept")
        if concept_name is not None and concept_name not in concept_ids:
            raise KeyError(f"metric {module_name}.{row['column_key']} names an unlisted concept '{concept_name}'")

        await session.execute(
            text("""
                INSERT INTO metrics (
                    id, module_id, concept_id, column_key, display_name,
                    value_type, unit, direction, property_categories,
                    variant_kind, variant, provenance, notes
                )
                VALUES (
                    gen_random_uuid(), CAST(:module_id AS uuid), CAST(:concept_id AS uuid),
                    :column_key, :display_name,
                    CAST(:value_type AS metricvaluetype), :unit,
                    CAST(:direction AS direction), CAST(:property_categories AS varchar[]),
                    CAST(:variant_kind AS variantkind), :variant,
                    CAST(:provenance AS provenance), :notes
                )
                ON CONFLICT ON CONSTRAINT uq_metric_identity DO UPDATE
                    SET concept_id          = EXCLUDED.concept_id,
                        display_name        = EXCLUDED.display_name,
                        value_type          = EXCLUDED.value_type,
                        unit                = EXCLUDED.unit,
                        direction           = EXCLUDED.direction,
                        property_categories = EXCLUDED.property_categories,
                        provenance          = EXCLUDED.provenance,
                        notes               = EXCLUDED.notes
                """),
            {
                "module_id": module_ids[module_name],
                "concept_id": concept_ids[concept_name] if concept_name else None,
                "column_key": row["column_key"],
                "display_name": row["display_name"],
                "value_type": row["value_type"],
                "unit": row.get("unit"),
                "direction": row.get("direction", "NEUTRAL"),
                "property_categories": row.get("property_categories", []),  # list, not '{...}' — see modules above
                "variant_kind": row.get("variant_kind"),
                "variant": row.get("variant"),
                "provenance": row.get("provenance", "INFERRED"),
                # The caveat that has to travel with the number (added in migration 006). NULL for
                # the uncurated metrics — an absent note is honest, an invented one is not.
                "notes": row.get("notes"),
            },
        )
    return len(rows)


async def _catalog_tables_exist(session: AsyncSession) -> bool:
    """Whether the three tables the upserts write to are present.

    Asked so the dry run can tell an ENVIRONMENT problem from a CATALOG one without enumerating
    exception types. Everything the upserts can raise arrives from the same place — the server,
    refusing our SQL — so the type alone is a poor discriminator, and the obvious shortlist is
    wrong: a CHECK violation surfaces as `IntegrityError`, but a bad enum value surfaces as bare
    `DBAPIError`, because asyncpg's InvalidTextRepresentationError has no DBAPI category to map
    onto. Both are the catalog being wrong. A list of types would have caught the first and missed
    the second, which is the failure item 7 of this cleanup already ran into once.

    So ask a positional question instead. If the connection works and these tables exist, then
    anything the upserts go on to raise is about the DATA we sent, and the environment is fine.

    `to_regclass` returns NULL rather than raising for a name that does not exist, which is what
    makes "unmigrated" a value here instead of another exception to classify.
    """
    present = await session.execute(
        text(
            "SELECT to_regclass('concepts') IS NOT NULL "
            "   AND to_regclass('modules')  IS NOT NULL "
            "   AND to_regclass('metrics')  IS NOT NULL"
        )
    )
    return bool(present.scalar_one())


async def seed(dry_run: bool = False, catalog_path: Path | None = None) -> Exit:
    """Seed the catalog, or preview it. Returns the process exit status.

    See `Exit` for what each status means and why it mirrors the real run. In short: 0 predicts a
    successful real run, and anything else predicts a failed one — including a database the preview
    could not look at, because the real run needs that same database and would fail on it too.

    Widening the dry run's exception handling so an unmigrated database stopped crashing also made
    it exit 0, which turned `--dry-run && seed_catalog.py` into a gate that opened on a schema that
    does not exist. Nothing scripts this today (no CI job, no shell script, no Python caller), so
    the contract is being set now rather than changed later.

    `catalog_path` defaults to seed/catalog.json. It exists so a candidate file can be validated
    before it replaces the real one — and so tests/test_seed_catalog_cli.py can drive the three
    exit states from a subprocess. That has to be a subprocess: called in-process, `seed()` would
    meet the conftest quarantine on `AsyncSessionLocal`, get UnboundExecutionError, and report
    COULD_NOT_VERIFY for a reason that has nothing to do with a database.
    """
    catalog_file = catalog_path if catalog_path is not None else _CATALOG
    catalog = json.loads(catalog_file.read_text(encoding="utf-8"))
    concepts = catalog["concepts"]
    modules = catalog["modules"]
    metrics = catalog["metrics"]

    if dry_run:
        # `relative_to` RAISES rather than falling back when the path is outside the repo, which a
        # --catalog argument routinely is. Ask, do not assume.
        shown = catalog_file.relative_to(_REPO_ROOT) if catalog_file.is_relative_to(_REPO_ROOT) else catalog_file
        print(f"{shown} parses: ")
        print(f"  {len(concepts)} concepts, {len(modules)} modules, {len(metrics)} metrics")
        named_modules = {m["name"] for m in modules}
        named_concepts = {c["name"] for c in concepts}
        # Same referential checks the real run makes, without touching the database.
        # Counted, not just printed. These lines predate the exit status and were reported into a
        # run that exited 0 regardless, so a preview could name a broken catalog and still look
        # like a pass.
        problems = 0
        could_not_verify = False
        for m in metrics:
            if m["module"] not in named_modules:
                print(f"  ERROR: metric {m['module']}.{m['column_key']} names an unlisted module")
                problems += 1
            if (c := m.get("concept")) and c not in named_concepts:
                print(f"  ERROR: metric {m['module']}.{m['column_key']} names an unlisted concept '{c}'")
                problems += 1

        # The heading invariant cannot be answered from the JSON alone: the violation that actually
        # bites is a curated INTERFACE row landing beside a BARE row that `seed_metric_skeleton`
        # already committed, and that row exists only in the database. So the dry run applies the
        # upserts, asks, and returns without committing. It stays useful offline — the checks above
        # ran, and a database it cannot use is reported rather than fatal — because editing
        # catalog.json on a machine with no Postgres, or one not yet migrated, is a real thing to
        # want to do.
        try:
            async with AsyncSessionLocal() as session:
                if not await _catalog_tables_exist(session):
                    print("  heading invariant: NOT CHECKED — the catalog tables do not exist here")
                    print("    (run `alembic upgrade head` against this database)")
                    could_not_verify = True
                else:
                    try:
                        concept_ids = await _upsert_concepts(session, concepts)
                        module_ids = await _upsert_modules(session, modules)
                        await _upsert_metrics(session, metrics, module_ids, concept_ids)
                        violations = await find_heading_violations(session)
                        for violation in violations:
                            print(f"  ERROR: {violation.describe()}")
                            problems += 1
                        print(
                            f"  heading invariant: {'would ABORT the real run' if violations else 'clean'} "
                            f"({len(violations)} violation(s))"
                        )
                    except Exception as refused:
                        # Reached only after the tables were confirmed present, so the connection
                        # and the schema are both fine and this is the server refusing our DATA —
                        # a CHECK violation, a bad enum value, a FK that does not resolve. That is
                        # the catalog being wrong, and re-running against a healthy database would
                        # fail identically, which is exactly what CATALOG_INVALID means.
                        detail = str(refused).splitlines()[0].strip()
                        print(f"  ERROR: the database refused this catalog — {type(refused).__name__}: {detail}")
                        problems += 1
        # Broad on purpose, and this was `except OSError` — which caught only two of the five ways
        # a database can be unusable. Measured, all five, against a real server:
        #
        #     connection refused   ConnectionRefusedError    OSError
        #     no such host         gaierror                  OSError
        #     no schema            ProgrammingError          SQLAlchemyError
        #     wrong password       InvalidPasswordError      asyncpg's own
        #     no such database     InvalidCatalogNameError   asyncpg's own
        #
        # Three unrelated hierarchies, and no reason to think that list is complete — asyncpg raises
        # its own at connect time, before SQLAlchemy has anything to wrap. An unmigrated database is
        # the one that actually happened: `relation "concepts" does not exist`, an unhandled
        # traceback out of a --dry-run whose entire promise is that it is safe to run and tells you
        # what the real run would do.
        #
        # Enumerating the hierarchies would be a guess that fails silently the next time one is
        # added. So catch everything and report precisely instead: the message names the exception
        # rather than asserting a cause, so a genuine bug in the upserts above reads as its own type
        # here rather than being laundered into "no database".
        except Exception as unusable:
            # First line only: `str()` on a SQLAlchemy DBAPIError appends the whole statement and
            # its bound parameters, which buries a one-line diagnosis in a screen of SQL that reads
            # like the traceback this branch exists to prevent.
            detail = str(unusable).splitlines()[0].strip()
            print(f"  heading invariant: NOT CHECKED — {type(unusable).__name__}: {detail}")
            # Not an error in the catalog, but not a clean bill of health either: half the checks
            # did not run, and the real run needs the same database. A human editing catalog.json
            # offline still gets the full report above; only the status differs, and only a script
            # reads that.
            could_not_verify = True
        print("(dry run — rolled back, nothing committed)")

        if problems:
            return Exit.CATALOG_INVALID  # certainly wrong, and certainly needs the JSON edited
        if could_not_verify:
            return Exit.COULD_NOT_VERIFY
        return Exit.CLEAN

    # One transaction for the whole seed: a half-applied catalog (modules without their metrics)
    # would be worse than no catalog, and the whole thing is small enough to commit atomically.
    async with AsyncSessionLocal() as session:
        concept_ids = await _upsert_concepts(session, concepts)
        module_ids = await _upsert_modules(session, modules)
        n_metrics = await _upsert_metrics(session, metrics, module_ids, concept_ids)

        # Before the commit, so a catalog that breaks (module, column_key) -> variant_kind rolls
        # back rather than landing. The database cannot express that dependency; see
        # app/catalog/invariants.py.
        await raise_on_heading_violations(session, source="seed_catalog")

        await session.commit()

    print(f"seeded {len(concept_ids)} concepts, {len(module_ids)} modules, {n_metrics} metrics")
    # The real run reports failure by raising, not by returning: `raise_on_heading_violations`
    # aborts before the commit, and anything the database refuses propagates. Reaching here means
    # it committed.
    return Exit.CLEAN


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the metric catalog from seed/catalog.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and validate; applies the writes, then rolls back without committing",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help="catalog JSON to use instead of seed/catalog.json (validate a candidate file)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(seed(dry_run=args.dry_run, catalog_path=args.catalog)))


if __name__ == "__main__":
    main()
