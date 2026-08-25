"""Catalog-side logic shared by the seeding scripts and the GraphQL resolvers.

Neither "importer" nor "graphql" is the right home for this: the same derivation has to run at
seed time (to decide what metric rows exist) and at query time (to decide which one a score key
means). A neutral package is what keeps those two from drifting apart.

THIS FILE MUST CONTAIN NO IMPORTS. NOT A STYLE RULE — THE APPLICATION STOPS IMPORTING.
--------------------------------------------------------------------------------------
``app/models/orm.py`` imports ``app.catalog.variant_kind``, and importing any submodule of a package
runs this file first. ``app.catalog.keys`` imports the ORM back (for ``ChainRole``). So a single
convenience re-export here closes the loop::

    app.models.orm  ->  app.catalog.variant_kind  ->  app.catalog.__init__  ->  app.catalog.keys
                                                                                      |
                    +-------- partially initialised, ChainRole not yet defined --------+

    AttributeError: partially initialized module 'app.models.orm' has no attribute
    'ChainRole' (most likely due to a circular import)

It fails at import, so it takes the whole app down rather than one code path — and the traceback
points at ``keys.py``'s ``_CHAIN_SUFFIX``, which is nowhere near the line that caused it.

``tests/test_catalog_package_has_no_imports.py`` fails if an import is added here, because a
docstring is not a guard.
"""
