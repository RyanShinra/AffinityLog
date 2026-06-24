import csv
import io
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.importer.column_mapping import REQUIRED_FIELDS, normalize_column
from app.models.orm import Candidate

logger = logging.getLogger(__name__)


@dataclass
class ImportResult:
    rows_processed: int = 0
    rows_imported: int = 0
    rows_skipped: list[dict] = field(default_factory=list)
    column_mapping_warnings: list[str] = field(default_factory=list)
    unrecognized_columns: list[str] = field(default_factory=list)


class ImportError(Exception):
    """Raised when the CSV file is structurally invalid and cannot be imported."""


def _parse_optional_float(value: str, column: str, row_num: int) -> float | None:
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        logger.warning("Row %d: could not parse float for %s=%r, storing None", row_num, column, value)
        return None


async def import_candidates_from_csv(
    content: bytes,
    experiment_id: uuid.UUID,
    db: AsyncSession,
) -> ImportResult:
    """
    Parse a Bio Discovery CSV export and bulk-insert candidates.

    Hard-fails (raises ImportError) on:
    - Completely unparseable file
    - Missing required columns (sequence_id, fasta_sequence)

    Soft-fails (skips row, records reason) on:
    - Empty sequence_id or fasta_sequence in a specific row
    """
    result = ImportResult()

    try:
        text = content.decode("utf-8-sig")  # handle BOM from Excel exports
    except UnicodeDecodeError:
        try:
            text = content.decode("latin-1")
        except Exception as exc:
            raise ImportError(f"File encoding could not be determined: {exc}") from exc

    if not text.strip():
        raise ImportError("Uploaded file is empty.")

    # Strip leading comment/metadata lines (e.g. Bio Discovery export headers start with #)
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    if not lines:
        raise ImportError("Uploaded file contains only comments — no data found.")
    text = "\n".join(lines)

    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        raise ImportError("Could not read CSV headers — file may be malformed.")

    raw_headers: list[str] = list(reader.fieldnames)
    col_map: dict[str, str] = {}  # raw_header -> normalized_field
    unrecognized: list[str] = []

    for raw in raw_headers:
        normalized = normalize_column(raw)
        if normalized:
            col_map[raw] = normalized
        else:
            unrecognized.append(raw)

    result.unrecognized_columns = unrecognized
    if unrecognized:
        result.column_mapping_warnings.append(
            f"Unrecognized columns will be stored in raw_scores: {unrecognized}"
        )

    mapped_normalized = set(col_map.values())
    missing_required = REQUIRED_FIELDS - mapped_normalized
    if missing_required:
        raise ImportError(
            f"CSV is missing required columns: {missing_required}. "
            f"Found headers: {raw_headers}"
        )

    candidates_to_add: list[Candidate] = []

    for row_num, row in enumerate(reader, start=2):  # start=2: row 1 is header
        result.rows_processed += 1

        # Build normalized field dict and raw_scores bucket
        normalized_row: dict[str, str] = {}
        raw_scores: dict[str, str] = {}

        for raw_header, value in row.items():
            if raw_header is None:
                continue
            normalized = col_map.get(raw_header)
            if normalized:
                normalized_row[normalized] = value
            else:
                raw_scores[raw_header] = value

        sequence_id = normalized_row.get("sequence_id", "").strip()
        fasta_sequence = normalized_row.get("fasta_sequence", "").strip()

        if not sequence_id:
            result.rows_skipped.append({"row": row_num, "reason": "missing sequence_id"})
            continue
        if not fasta_sequence:
            result.rows_skipped.append({"row": row_num, "reason": "missing fasta_sequence"})
            continue

        candidate = Candidate(
            experiment_id=experiment_id,
            sequence_id=sequence_id,
            fasta_sequence=fasta_sequence,
            binding_affinity_kd=_parse_optional_float(
                normalized_row.get("binding_affinity_kd", ""), "binding_affinity_kd", row_num
            ),
            humanness_score=_parse_optional_float(
                normalized_row.get("humanness_score", ""), "humanness_score", row_num
            ),
            aggregation_propensity=_parse_optional_float(
                normalized_row.get("aggregation_propensity", ""), "aggregation_propensity", row_num
            ),
            raw_scores=raw_scores,
        )
        candidates_to_add.append(candidate)
        result.rows_imported += 1

    db.add_all(candidates_to_add)
    await db.commit()

    return result
