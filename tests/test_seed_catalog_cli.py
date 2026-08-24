"""The exit contract of `scripts/seed_catalog.py --dry-run`.

The dry run's status is a PREDICTION of what the real run would do — 0 if it would succeed,
non-zero if it would not — refined into two failures that need different things done about them.
`Exit` in that script carries the reasoning; this file pins the behaviour.

WHY SUBPROCESSES. Calling `seed()` in-process would meet the conftest quarantine on
`AsyncSessionLocal`, raise UnboundExecutionError inside the dry run's broad `except`, and report
COULD_NOT_VERIFY — the right answer for entirely the wrong reason, on every case, including the
ones that should be CLEAN. A subprocess is the only way to exercise the contract rather than the
test harness. It also covers `main()`, so `SystemExit(Exit.X)` reaching the shell as a status is
part of what is asserted.

EVERY CASE SETS DATABASE_URL. Without it the child falls back to `.env`, which points at the
development corpus — so a test that merely forgot would run against real data and pass locally
while failing in CI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "seed_catalog.py"

# A syntactically valid address with nothing listening: connection refused, fast and offline.
_NO_DATABASE = "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/absent"


def _dry_run(database_url: str, catalog: Path | None = None, extra: list[str] | None = None) -> int:
    """Run the script as the shell would, and return its exit status."""
    argv = [sys.executable, str(_SCRIPT), "--dry-run"]
    if catalog is not None:
        argv += ["--catalog", str(catalog)]
    completed = subprocess.run(
        argv + (extra or []),
        cwd=_REPO_ROOT,
        env={"PATH": "/usr/bin:/bin", "DATABASE_URL": database_url, "HOME": str(Path.home())},
        capture_output=True,
        text=True,
        timeout=120,
    )
    return completed.returncode


def _catalog_naming_an_unlisted_module(tmp_path: Path) -> Path:
    """The cheapest genuinely-invalid catalog: a metric whose module is not declared."""
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "concepts": [],
                "modules": [],
                "metrics": [
                    {
                        "module": "no_such_module",
                        "column_key": "bogus",
                        "display_name": "Bogus",
                        "value_type": "FLOAT",
                    }
                ],
            }
        )
    )
    return path


class TestOfflineStates:
    """Two of the three need no database, which is why they are worth having."""

    def test_a_database_it_cannot_reach_could_not_verify(self) -> None:
        """Not an error in the catalog — but the real run needs that database too, so not success."""
        assert _dry_run(_NO_DATABASE) == 3

    def test_a_broken_catalog_outranks_an_unusable_database(self, tmp_path: Path) -> None:
        """Both wrong at once. CATALOG_INVALID wins: it is the one certainly true and certainly
        needing a fix, and an unreachable database cannot make a broken catalog un-broken.

        The plain CATALOG_INVALID case — only the catalog wrong — needs a working database and so
        lives below. The two were briefly the same test, which tested precedence not at all.
        """
        assert _dry_run(_NO_DATABASE, catalog=_catalog_naming_an_unlisted_module(tmp_path)) == 1

    def test_a_usage_error_is_argparses_two_and_stays_that_way(self) -> None:
        """2 is not ours. It is why COULD_NOT_VERIFY is 3 and not 2."""
        assert _dry_run(_NO_DATABASE, extra=["--nonsense"]) == 2


class TestAgainstARealDatabase:
    def test_a_catalog_naming_an_unlisted_module_is_invalid(self, database_url: str, tmp_path: Path) -> None:
        """Only the catalog is wrong — the database is fine — so 1 cannot be blamed on the database."""
        assert _dry_run(database_url, catalog=_catalog_naming_an_unlisted_module(tmp_path)) == 1

    def test_a_row_the_schema_refuses_is_a_catalog_problem(self, database_url: str, tmp_path: Path) -> None:
        """A CHECK violation is the JSON being wrong, not the database being unavailable.

        `variant_kind` without `variant` passes the referential pre-check — the module exists — and
        is refused by migration 008's `ck_metric_variant_pair` at INSERT time. Before the schema
        probe this returned 3, blaming a database that was working perfectly.
        """
        catalog = tmp_path / "catalog.json"
        catalog.write_text(
            json.dumps(
                {
                    "concepts": [],
                    "modules": [{"name": "probe_module", "module_type": "SCORE", "functions": []}],
                    "metrics": [
                        {
                            "module": "probe_module",
                            "column_key": "probe_col",
                            "display_name": "P",
                            "value_type": "FLOAT",
                            "variant_kind": "PARAMETER",  # and no `variant` — the CHECK refuses this
                        }
                    ],
                }
            )
        )

        assert _dry_run(database_url, catalog=catalog) == 1

    def test_a_driver_error_with_no_dbapi_category_is_still_a_catalog_problem(self, database_url: str, tmp_path: Path) -> None:
        """The case a list of exception types would have missed.

        A bad enum value surfaces as bare `DBAPIError`, not `DataError`: asyncpg's
        InvalidTextRepresentationError has no DBAPI category to map onto. Classifying on type would
        have caught the CHECK violation above and silently misfiled this one, which is why the split
        is positional — the tables were confirmed present, so the server is refusing our DATA.
        """
        catalog = tmp_path / "catalog.json"
        catalog.write_text(
            json.dumps(
                {
                    "concepts": [],
                    "modules": [{"name": "probe_module", "module_type": "SCORE", "functions": []}],
                    "metrics": [
                        {
                            "module": "probe_module",
                            "column_key": "probe_col",
                            "display_name": "P",
                            "value_type": "NOT_A_REAL_TYPE",  # not a metricvaluetype member
                        }
                    ],
                }
            )
        )

        assert _dry_run(database_url, catalog=catalog) == 1

    def test_the_real_catalog_against_a_migrated_database_is_clean(self, database_url: str) -> None:
        """The only state that needs a database, run against the suite's own empty container.

        Uses seed/catalog.json rather than a fixture, so this fails if the committed catalog ever
        stops being seedable — which is the question a reader of exit 0 actually cares about.
        """
        assert _dry_run(database_url) == 0
