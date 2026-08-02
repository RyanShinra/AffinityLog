"""Guard the three-way coupling on the interface-kind strings — no database required.

WHAT COULD GO WRONG
-------------------
The same three strings ("antibody-target complex", "antibody only (H/L pairing)", "single chain
(no interface)") are written down in three independent places that must agree byte for byte:

    1. the CASE arms in sql/candidate_summary.sql          — what the view emits
    2. the CASE arms in migrations/versions/004_*.py       — what a fresh clone's view emits
    3. the `variant` of the nine INTERFACE rows in seed/catalog.json — what the catalog looks up by

plus, now, the `InterfaceKind` enum. Nothing else compares them. Rewording one CASE arm would leave
`boltz2.iptm`, `boltz2.protein_iptm` and `boltz2.complex_ipde` — the three interface-dependent keys
in the corpus — resolving to no catalog row at all. No exception, no failing test: the scores would
simply stop carrying meaning, which is the *one* thing the catalog exists to provide.

`scripts/check_view_migration.py` does not cover this. It compares the view's output **column
aliases**, so a reworded THEN-literal passes it untouched: same columns, different values.

WHY THIS NEEDS NO DATABASE
--------------------------
Every source above is a file in the repo. Checking the .sql *and* the migration closes the chain
end to end — the migration is what actually creates the view in any given database, so if both files
agree with the enum, the DB cannot disagree without the migration being bypassed. An assertion
against a live view would only ever prove three of the four arms anyway (nothing in the corpus
reaches `no chains recorded`), so it would be the weakest of the checks, not the strongest.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.catalog.interface_kind import InterfaceKind

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SQL_FILE = _REPO_ROOT / "sql" / "candidate_summary.sql"
_MIGRATION = _REPO_ROOT / "migrations" / "versions" / "004_add_candidate_summary_view.py"
_CATALOG = _REPO_ROOT / "seed" / "catalog.json"

_LINE_COMMENT = re.compile(r"--[^\n]*")
# Only the RESULT of a branch, never its condition. This is the whole subtlety of the parse: the
# CASE also contains 'TARGET' and 'LIGHT' inside `bool_or(cc.chain::text = 'TARGET')`, which are
# chain labels, not interface kinds. Anchoring on THEN/ELSE takes the four results and skips them.
_BRANCH_RESULT = re.compile(r"\b(?:THEN|ELSE)\s+'([^']*)'", re.IGNORECASE)


def case_arms(text: str) -> list[str]:
    """Extract the interface_kind CASE's branch results, in the order the SQL evaluates them.

    Located by its `END AS interface_kind` tail and walked back to the nearest preceding CASE, so
    this stays correct if another CASE is ever added to the view. Comments are stripped first —
    the migration's prose above the CASE discusses the ELSE branch in English.
    """
    body = _LINE_COMMENT.sub("", text)
    end = re.search(r"\bEND\s+AS\s+interface_kind\b", body, re.IGNORECASE)
    assert end is not None, "no `END AS interface_kind` found — has the view been restructured?"
    start = body.rfind("CASE", 0, end.start())
    assert start != -1, "found `END AS interface_kind` with no matching CASE"
    return _BRANCH_RESULT.findall(body[start : end.start()])


def catalog_interface_variants() -> set[str]:
    """The `variant` values of every INTERFACE-kind metric in the seed catalog."""
    metrics = json.loads(_CATALOG.read_text(encoding="utf-8"))["metrics"]
    return {m["variant"] for m in metrics if m.get("variant_kind") == "INTERFACE"}


class TestEnumMatchesTheSql:
    """The enum is only useful if it says exactly what the view says."""

    def test_sql_file_case_arms_match_the_enum_exactly(self) -> None:
        # Order included on purpose. The CASE is order-dependent — the count() guard must come first
        # (bool_or over zero rows is NULL, not false) and TARGET must precede LIGHT (an H/L/T fold
        # also has a light chain, but it is a complex, not a pairing). The enum is declared in that
        # same order, so comparing lists documents the evaluation order rather than just the set.
        assert case_arms(_SQL_FILE.read_text(encoding="utf-8")) == [k.value for k in InterfaceKind]

    def test_migration_case_arms_match_the_enum_exactly(self) -> None:
        # The migration, not the .sql file, is what creates the view in any real database — a fresh
        # clone never reads sql/. Checking both is what makes the file-only approach airtight.
        assert case_arms(_MIGRATION.read_text(encoding="utf-8")) == [k.value for k in InterfaceKind]

    def test_the_parse_ignores_chain_labels_in_the_conditions(self) -> None:
        # A guard on the guard: if _BRANCH_RESULT ever slipped to matching any quoted literal, the
        # tests above would still pass on a longer list only by accident. 'TARGET'/'LIGHT' appear
        # in the CASE as chain comparisons and must never be read as interface kinds.
        arms = case_arms(_SQL_FILE.read_text(encoding="utf-8"))
        assert "TARGET" not in arms
        assert "LIGHT" not in arms
        assert len(arms) == 4


class TestEnumMatchesTheCatalog:
    """The catalog looks metrics up BY these strings, so a mismatch silently unresolves scores."""

    def test_every_catalog_variant_is_an_enum_member(self) -> None:
        assert catalog_interface_variants() <= {k.value for k in InterfaceKind}

    def test_catalog_covers_the_three_real_arms_but_not_the_data_quality_one(self) -> None:
        # The asymmetry is deliberate and worth pinning down: three biological arms get metric rows,
        # 'no chains recorded' does not. A candidate with no chains therefore resolves to NO metric,
        # which is exactly why ScoreEntry.metric is nullable. If someone later seeds a fourth row,
        # this fails and forces that decision to be made on purpose.
        assert catalog_interface_variants() == {k.value for k in InterfaceKind} - {InterfaceKind.NO_CHAINS_RECORDED.value}
