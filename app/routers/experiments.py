import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.importer.csv_importer import ImportError, import_candidates_from_csv
from app.models.orm import Experiment
from app.schemas.pydantic import ExperimentCreate, ExperimentResponse, ImportResponse

router = APIRouter(prefix="/experiments", tags=["experiments"])


@router.post("", response_model=ExperimentResponse, status_code=status.HTTP_201_CREATED)
async def create_experiment(
    body: ExperimentCreate,
    db: AsyncSession = Depends(get_db),
) -> Experiment:
    experiment = Experiment(**body.model_dump())
    db.add(experiment)
    await db.commit()
    await db.refresh(experiment)
    return experiment


@router.get("", response_model=list[ExperimentResponse])
async def list_experiments(
    db: AsyncSession = Depends(get_db),
) -> list[Experiment]:
    result = await db.execute(select(Experiment).order_by(Experiment.created_at.desc()))
    return list(result.scalars().all())


@router.get("/{experiment_id}", response_model=ExperimentResponse)
async def get_experiment(
    experiment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Experiment:
    experiment = await db.get(Experiment, experiment_id)
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return experiment


@router.post("/{experiment_id}/candidates/import", response_model=ImportResponse)
async def import_candidates(
    experiment_id: uuid.UUID,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
) -> ImportResponse:
    experiment = await db.get(Experiment, experiment_id)
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    content = await file.read()

    try:
        result = await import_candidates_from_csv(content, experiment_id, db)
    except ImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Record the source filename for traceability
    experiment.source_filename = file.filename
    await db.commit()

    return ImportResponse(
        rows_processed=result.rows_processed,
        rows_imported=result.rows_imported,
        rows_skipped=result.rows_skipped,
        column_mapping_warnings=result.column_mapping_warnings,
        unrecognized_columns=result.unrecognized_columns,
    )
