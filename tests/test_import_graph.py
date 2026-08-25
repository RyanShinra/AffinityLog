"""The import graph must stay acyclic, and two properties keep it that way.

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

THE TWO CHECKS ARE COMPLEMENTARY, NOT REDUNDANT. The static one catches an import that is merely
BOUND — the gun loaded but not fired, which does not raise until something uses it. The cold-import
one catches the graph actually failing to resolve, including shapes the static check cannot see.
A guard with only one of them has a hole; the first review of this file found exactly that.

WHAT IS DERIVED RATHER THAN LISTED. Both sets below come from the code, because the failure mode of
a hand-maintained list is SILENCE: a new `from app.catalog.x import ...` in the ORM would simply go
unchecked while every test stayed green. `test_the_derivations_found_something` is what makes that
loud, in the same spirit as the collector tests in `test_postgres_enum_labels.py`.

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
_MODELS: Final[Path] = _REPO_ROOT / "app" / "models"

# Every module the server loads. `app.main` is the real entry point — naming only `app.graphql.schema`
# left `app.models.views` and all three routers unreached, so a cycle through any of them would have
# failed at uvicorn start and nowhere else. The other two are the two ends of the cycle itself.
_ENTRY_POINTS: Final[tuple[str, ...]] = ("app.main", "app.models.orm", "app.catalog.keys")


def _first_party_imports(path: Path) -> list[ast.stmt]:
    """Every import of our own code in a file. Stdlib is not the hazard — a cycle needs two of OURS.

    `enum` and `typing` are what the leaves are made of, so forbidding all imports would forbid the
    files themselves.

    RELATIVE IMPORTS COUNT, and are the reason this is not a one-line prefix test. `from . import
    keys` has `module=None` and `from ..models import orm` has `module="models"` — neither starts
    with "app", so a name-prefix check alone waves both through. They are first-party by
    construction: a relative import cannot reach outside the package.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[ast.stmt] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level > 0 or (node.module or "").startswith("app"):
                found.append(node)
        elif isinstance(node, ast.Import) and any(a.name.startswith("app") for a in node.names):
            found.append(node)
    return found


def _modules_the_orm_reaches() -> tuple[str, ...]:
    """The catalog modules `app/models/` imports, plus the package `__init__` that runs with them.

    Derived, not listed. Importing `app.catalog.anything` executes `app/catalog/__init__.py` first,
    so that file is always on the path whatever else is.
    """
    reached = {"__init__.py"}
    for source in sorted(_MODELS.glob("*.py")):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"), filename=str(source))):
            if isinstance(node, ast.ImportFrom) and node.module is not None and node.module.startswith("app.catalog."):
                reached.add(node.module.removeprefix("app.catalog.").split(".")[0] + ".py")
    return tuple(sorted(reached))


def test_the_derivations_found_something() -> None:
    """If `_modules_the_orm_reaches` returns nothing, every test below silently covers nothing.

    That is the failure a guard must not have, so it gets its own assertion rather than being
    trusted — the same reason `test_postgres_enum_labels.py` tests its own collector.
    """
    reached = _modules_the_orm_reaches()

    assert "variant_kind.py" in reached, f"app/models/ imports VariantKind; derivation found {reached}"
    assert "identifiers.py" in reached, f"app/models/ imports the identifier aliases; derivation found {reached}"
    assert "__init__.py" in reached, "importing any app.catalog submodule runs the package __init__"


@pytest.mark.parametrize("filename", _modules_the_orm_reaches())
def test_the_modules_the_orm_reaches_are_leaves(filename: str) -> None:
    offenders = _first_party_imports(_CATALOG / filename)

    assert not offenders, (
        f"app/catalog/{filename} must import nothing from `app`, but has {len(offenders)} "
        f"on line(s) {[n.lineno for n in offenders]}.\n"
        "app/models/ imports this file (or the package that contains it), and app/catalog/keys.py "
        "imports the ORM back. An import here closes that loop and the application fails to import "
        "with an AttributeError about ChainRole, raised from keys.py.\n"
        "If you need something here, put it in a module the ORM does not reach."
    )


@pytest.mark.parametrize("module", _ENTRY_POINTS)
def test_it_can_be_imported_first_on_a_cold_interpreter(module: str) -> None:
    """The check above cannot make: that the graph as a whole actually resolves."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"`import {module}` fails on a cold interpreter. If the traceback below says 'circular "
        f"import', the graph has a cycle; anything else is an ordinary import error. Neither "
        f"reproduces in-process — by the time a test runs, sys.modules is populated and every "
        f"order works.\n\n{result.stderr.strip()[-800:]}"
    )


def test_the_leaf_files_still_explain_why() -> None:
    """The tests say imports are forbidden; only the files can say what happens if you add one."""
    for filename in _modules_the_orm_reaches():
        source = (_CATALOG / filename).read_text(encoding="utf-8")
        assert "circular import" in source, f"app/catalog/{filename} should say what its leafness protects"
