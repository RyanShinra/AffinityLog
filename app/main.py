from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine
from app.graphql.schema import get_graphql_router
from app.routers import experiments, health

app = FastAPI(
    title=settings.app_title,
    version=settings.app_version,
    description=(
        "AffinityLog: backend API for ingesting Amazon Bio Discovery antibody design exports. "
        "REST endpoint for CSV upload; GraphQL for querying and comparing candidates."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(experiments.router)
app.include_router(get_graphql_router(engine), prefix="/graphql")
