from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
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

# Vendored third-party assets (3Dmol.js). Served locally so the demo needs no network at all —
# see app/static/vendor/3Dmol-LICENSE.txt for the BSD-3-Clause notice it ships under.
app.mount("/static", StaticFiles(directory=str(_REPO_ROOT / "app" / "static")), name="static")
