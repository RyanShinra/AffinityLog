"""The import graph must stay acyclic, and two specific properties keep it that way.

WHY THIS FILE EXISTS. `app/models/orm.py` imports `app/catalog/`, and `app/catalog/keys.py` imports
the ORM back — for `ChainRole`, which `_CHAIN_SUFFIX` is derived from. That is legal only because
the modules the ORM reaches for are LEAVES. Break either property below and the whole application
stops importing, with:

    AttributeError: partially initialized module 'app.models.orm' has no attribute 'ChainRole'
    (most likely due to a circular import)

raised from `keys.py`'s `_CHAIN_SUFFIX` — nowhere near the line at fault, which is what makes this
class of bug expensive. Both properties were load-bearing before these tests existed and were
recorded only in `docs/type-safety-plan.md`; a constraint that lives in a plan is one nobody opening
the file will meet.

THE COLD-IMPORT TESTS RUN IN A SUBPROCESS on purpose. By the time pytest reaches this module,
everything is already in `sys.modules` and every order works — the failure only exists on a cold
interpreter, and only for whichever module happens to be imported first. That is genuinely
process-start state, which is the one case where reaching for a subprocess is right rather than
lazy (see the monkeypatch convention in CLAUDE.md).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
_CATALOG: Final[Path] = _REPO_ROOT / "app" / "catalog"

# Every module the ORM can reach, directly or through the package it lives in. These must import
# nothing, because importing them is part of importing the ORM.
_LEAVES: Final[tuple[str, ...]] = ("__init__.py", "variant_kind.py", "identifiers.py")

# Importing any of these FIRST, on a cold interpreter, must work. `app.models.orm` and
# `app.catalog.keys` are the two ends of the cycle; `app.graphql.schema` is what the server builds.
_ENTRY_POINTS: Final[tuple[str, ...]] = ("app.models.orm", "app.catalog.keys", "app.graphql.schema")


def _first_party_imports(path: Path) -> list[ast.stmt]:
    """Every `app.*` import in a file. Stdlib is not the hazard — a cycle needs two of OUR modules.

    `enum` and `typing` are exactly what these leaves are made of, so forbidding all imports would
    forbid the files themselves. The rule is narrower and is the real one: nothing that could import
    back.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[ast.stmt] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("app")
            or isinstance(node, ast.Import)
            and any(a.name.startswith("app") for a in node.names)
        ):
            found.append(node)
    return found


@pytest.mark.parametrize("filename", _LEAVES)
def test_the_modules_the_orm_reaches_are_leaves(filename: str) -> None:
    offenders = _first_party_imports(_CATALOG / filename)

    assert not offenders, (
        f"app/catalog/{filename} must import nothing from `app`, but has {len(offenders)} "
        f"on line(s) {[n.lineno for n in offenders]}.\n"
        "app/models/orm.py imports this file (or the package that contains it), and "
        "app/catalog/keys.py imports the ORM back. An import here closes that loop and the "
        "application fails to import with an AttributeError about ChainRole, raised from keys.py.\n"
        "If you need something here, put it in a module the ORM does not reach."
    )


@pytest.mark.parametrize("module", _ENTRY_POINTS)
def test_it_can_be_imported_first_on_a_cold_interpreter(module: str) -> None:
    """The check `test_..._are_leaves` cannot make: that the graph as a whole actually resolves."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"`import {module}` fails on a cold interpreter, which means the import graph has a cycle. "
        f"Nothing in-process will reproduce this — by the time a test runs, sys.modules is already "
        f"populated and every order works.\n\n{result.stderr.strip()[-800:]}"
    )


def test_the_leaf_files_still_explain_why() -> None:
    """The tests say imports are forbidden; only the files can say what happens if you add one."""
    for filename in _LEAVES:
        source = (_CATALOG / filename).read_text(encoding="utf-8")
        assert "circular import" in source, f"app/catalog/{filename} should say what its leafness protects"
