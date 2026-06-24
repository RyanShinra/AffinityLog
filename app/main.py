from fastapi import FastAPI

from app.config import settings
from app.routers import health

# Minimal application skeleton retained through the schema-first redesign.
# Only the /health probe is wired up; the REST routers and the GraphQL mount come
# back as the data model and API are rebuilt from the design discussion.
# Baseline (Sonnet scaffold) preserved at git tag v0-scaffold.
app = FastAPI(title=settings.app_title, version=settings.app_version)
app.include_router(health.router)
