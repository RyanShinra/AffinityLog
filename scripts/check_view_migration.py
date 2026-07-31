#!/usr/bin/env python3
"""Guard against `sql/candidate_summary.sql` drifting from the migration that actually applies it.

WHY THIS EXISTS
---------------
The view is deliberately written down twice: ``sql/candidate_summary.sql`` is the annotated copy
you iterate on in TablePlus, and ``migrations/versions/00X_add_candidate_summary_view.py`` holds
the snapshot Alembic really runs. Migrations must be immutable, so the migration can't just read
the file — which means the two can silently diverge, and then a fresh clone gets a *different*
view from the one the developer has been testing against.

That is exactly the bug class that bit us on 2026-07-31: the model declared ``binding_iptm``
after the view had been renamed to ``iptm``, and nothing caught it until the page 500'd.

WHAT IT CHECKS
--------------
The **ordered list of output column aliases** in both files. Comments, whitespace, and formatting
are ignored, so re-wording a comment won't fail the build — only an actual change to the view's
shape will. If they differ, you changed the `.sql` without cutting a new migration (or vice versa).

    python scripts/check_view_migration.py        # exit 0 = in sync, 1 = drifted
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SQL_FILE = _REPO_ROOT / "sql" / "candidate_summary.sql"
_MIGRATIONS_DIR = _REPO_ROOT / "migrations" / "versions"

# `AS alias` at the end of a select item. Applied after comments are stripped and after the
# leading `CREATE VIEW <name> AS` is removed, so the only matches left are column aliases.
_ALIAS = re.compile(r"\bAS\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_CREATE_VIEW = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+\w+\s+AS", re.IGNORECASE)


def column_aliases(text: str) -> list[str]:
    """Extract the ordered output column aliases from a CREATE VIEW statement.

    Parsing starts at ``CREATE VIEW … AS`` and stops at the end of the statement, which matters
    for the migration: everything before it is a Python docstring full of ordinary English, and
    prose like "as much as" would otherwise be picked up as column aliases.
    """
    start = _CREATE_VIEW.search(text)
    if start is None:
        return []
    body = text[start.end() :]
    end = body.find('"""')  # in the migration, the SQL ends at the closing triple quote
    if end != -1:
        body = body[:end]
    body = _LINE_COMMENT.sub("", body)  # SQL comments may contain "as" — drop them too
    return [m.group(1).lower() for m in _ALIAS.finditer(body)]


def latest_view_migration() -> Path:
    """The highest-numbered migration that creates the candidate_summary view."""
    candidates = sorted(_MIGRATIONS_DIR.glob("*_add_candidate_summary_view.py"))
    if not candidates:
        sys.exit("error: no *_add_candidate_summary_view.py migration found — did you cut one?")
    return candidates[-1]


def main() -> None:
    migration = latest_view_migration()
    from_sql = column_aliases(_SQL_FILE.read_text(encoding="utf-8"))
    from_migration = column_aliases(migration.read_text(encoding="utf-8"))

    if not from_sql:
        sys.exit(f"error: no column aliases parsed from {_SQL_FILE.name} — is the file intact?")

    if from_sql == from_migration:
        print(f"OK: {_SQL_FILE.name} and {migration.name} define the same {len(from_sql)} columns.")
        return

    only_sql = [c for c in from_sql if c not in from_migration]
    only_mig = [c for c in from_migration if c not in from_sql]
    print(f"DRIFT: {_SQL_FILE.name} and {migration.name} disagree.", file=sys.stderr)
    print(f"  {_SQL_FILE.name:<28} {from_sql}", file=sys.stderr)
    print(f"  {migration.name:<28} {from_migration}", file=sys.stderr)
    if only_sql:
        print(f"  only in the .sql file:  {only_sql}", file=sys.stderr)
    if only_mig:
        print(f"  only in the migration:  {only_mig}", file=sys.stderr)
    print(
        "\nThe .sql file is the copy you iterate on; the migration is what actually runs.\n"
        "Cut a new migration (copy the updated view into it) so a fresh clone gets this shape.",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
