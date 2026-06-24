from fastapi import APIRouter

from app.config import settings
from app.schemas.pydantic import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["ops"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=settings.app_version)
