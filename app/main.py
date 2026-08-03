from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from strawberry.fastapi import GraphQLRouter

from app.config import settings
from app.graphql.context import Context, get_context
from app.graphql.schema import schema
from app.routers import demo, health

# Anchored to this file, not the process working directory — same reasoning as app/routers/demo.py.
_REPO_ROOT = Path(__file__).resolve().parents[1]

# Minimal application skeleton retained through the schema-first redesign.
# Only the /health probe is wired up; the REST routers and the GraphQL mount come
# back as the data model and API are rebuilt from the design discussion.
# Baseline (Sonnet scaffold) preserved at git tag v0-scaffold.
app = FastAPI(title=settings.app_title, version=settings.app_version)
app.include_router(health.router)
app.include_router(demo.router)

# GraphQLRouter is an APIRouter subclass, which is the whole of how Strawberry attaches to FastAPI:
# it mounts like any other router, and `context_getter`'s parameters are filled in by FastAPI's
# ordinary dependency machinery before Strawberry sees them (see app/graphql/context.py).
#
# The generic parameters are [Context, RootValue]; RootValue is unused here, hence None. Declaring
# them is what lets mypy check that `info.context.session` in a resolver is really an AsyncSession.
#
# `graphql_ide="graphiql"` is the default and serves the in-browser playground on GET /graphql. It
# is fine for a local portfolio demo; a public deployment would want it off.
graphql_router: GraphQLRouter[Context, None] = GraphQLRouter(schema, context_getter=get_context)
app.include_router(graphql_router, prefix="/graphql")

# Vendored third-party assets (3Dmol.js). Served locally so the demo needs no network at all —
# see app/static/vendor/3Dmol-LICENSE.txt for the BSD-3-Clause notice it ships under.
app.mount("/static", StaticFiles(directory=str(_REPO_ROOT / "app" / "static")), name="static")
