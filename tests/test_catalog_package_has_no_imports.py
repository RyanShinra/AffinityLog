"""`app/catalog/__init__.py` must stay import-free, and this is what enforces it.

WHY A TEST AND NOT A COMMENT. `app/models/orm.py` imports `app.catalog.variant_kind`, so importing
the ORM runs `app/catalog/__init__.py` first; and `app.catalog.keys` imports the ORM back for
`ChainRole`. Any import added to that file therefore closes a cycle and the application stops
importing — with an `AttributeError` about `ChainRole` raised from `keys.py`, which is nowhere near
the line responsible.

That property was load-bearing before this test existed (it is why moving `VariantKind` into the
package worked at all) and was recorded only in `docs/type-safety-plan.md`. A constraint that lives
in a plan is one nobody reading `__init__.py` will meet.

Costs no database and no application import: the file is read and parsed, not executed.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_INIT: Final[Path] = Path(__file__).resolve().parents[1] / "app" / "catalog" / "__init__.py"


def test_the_catalog_package_init_imports_nothing() -> None:
    tree = ast.parse(_INIT.read_text(encoding="utf-8"), filename=str(_INIT))
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.Import | ast.ImportFrom)]

    assert not imports, (
        f"{_INIT.name} must contain no imports, but has {len(imports)} "
        f"(line(s) {[node.lineno for node in imports]}).\n"
        "app/models/orm.py imports app.catalog.variant_kind, which runs this file; app.catalog.keys "
        "imports the ORM back. An import here closes that loop and the whole application fails to "
        "import with an AttributeError about ChainRole, raised from keys.py.\n"
        "If you need a re-export, put it in a submodule and import THAT directly."
    )


def test_the_file_still_explains_why() -> None:
    """The test says an import is forbidden; only the docstring can say what happens if you add one."""
    source = _INIT.read_text(encoding="utf-8")

    assert "MUST CONTAIN NO IMPORTS" in source
    assert "circular import" in source
