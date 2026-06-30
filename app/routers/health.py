from fastapi import APIRouter

from app.config import settings

router = APIRouter()


# Kept through the redesign: the ECS/Docker health probe. Deliberately self-contained
# (returns a plain dict, no domain schema) so it survives clearing the application layer.
@router.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": settings.app_version}
