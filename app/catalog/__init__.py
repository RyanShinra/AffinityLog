"""Catalog-side logic shared by the seeding scripts and the GraphQL resolvers.

Neither "importer" nor "graphql" is the right home for this: the same derivation has to run at
seed time (to decide what metric rows exist) and at query time (to decide which one a score key
means). A neutral package is what keeps those two from drifting apart.
"""
