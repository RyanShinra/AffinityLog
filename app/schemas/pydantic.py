import uuid
from datetime import datetime

from pydantic import BaseModel


class ExperimentCreate(BaseModel):
    name: str
    recipe_name: str
    target_name: str
    target_pdb_id: str | None = None
    notes: str | None = None


class ExperimentResponse(BaseModel):
    id: uuid.UUID
    name: str
    recipe_name: str
    target_name: str
    target_pdb_id: str | None
    source_filename: str | None
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ImportResponse(BaseModel):
    rows_processed: int
    rows_imported: int
    rows_skipped: list[dict]
    column_mapping_warnings: list[str]
    unrecognized_columns: list[str]


class HealthResponse(BaseModel):
    status: str
    version: str
