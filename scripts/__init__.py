"""Operational scripts: seeders, corpus loaders, and the drift checks CI cannot run.

Nothing imports this package to *use* it — every script here is run as
``python scripts/<name>.py`` and the marker is not needed for that. It exists so the directory has
one module identity instead of two.

``tests/test_schema_snapshot.py`` does ``from scripts.dump_schema import ...`` to assert the
committed ``schema.graphql`` against what the code actually serves. Without this file, a root-level
``mypy .`` walked the tree and named that same file ``dump_schema``, met the import that names it
``scripts.dump_schema``, and stopped with "Source file found twice under different module names" —
before checking anything at all. CI never saw it, because CI runs ``mypy app/``.

Safe to add because ``[tool.setuptools.packages.find]`` in ``pyproject.toml`` is
``include = ["app*"]``, so this directory is still not installed into the environment.
"""
