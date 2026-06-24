"""Unit tests for the CSV importer — no database required."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.importer.column_mapping import normalize_column
from app.importer.csv_importer import ImportError, import_candidates_from_csv


def _make_db(candidates_added: list) -> AsyncMock:
    db = AsyncMock()
    db.add_all = MagicMock(side_effect=candidates_added.extend)
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Column mapping unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Kd (nM)", "binding_affinity_kd"),
        ("kd(nm)", "binding_affinity_kd"),
        ("KD", "binding_affinity_kd"),
        ("binding_affinity", "binding_affinity_kd"),
        ("humanness_score", "humanness_score"),
        ("humanness", "humanness_score"),
        ("oasis_score", "humanness_score"),
        ("aggregation_propensity", "aggregation_propensity"),
        ("aggrescan_score", "aggregation_propensity"),
        ("sequence_id", "sequence_id"),
        ("fasta_sequence", "fasta_sequence"),
        ("sequence", "fasta_sequence"),
        ("unknown_column_xyz", None),
    ],
)
def test_column_normalization(raw: str, expected: str | None) -> None:
    assert normalize_column(raw) == expected


# ---------------------------------------------------------------------------
# Importer happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_sample_fixture(sample_csv_bytes: bytes) -> None:
    added: list = []
    db = _make_db(added)
    exp_id = uuid.uuid4()

    result = await import_candidates_from_csv(sample_csv_bytes, exp_id, db)

    assert result.rows_imported == 35
    assert result.rows_processed == 35
    assert result.rows_skipped == []
    # stability_score and paratope_surface_area should end up in raw_scores
    assert "Unrecognized columns will be stored in raw_scores" in " ".join(
        result.column_mapping_warnings
    )
    assert len(added) == 35


@pytest.mark.asyncio
async def test_unrecognized_columns_land_in_raw_scores(sample_csv_bytes: bytes) -> None:
    added: list = []
    db = _make_db(added)
    exp_id = uuid.uuid4()

    await import_candidates_from_csv(sample_csv_bytes, exp_id, db)

    # Every candidate should have stability_score and paratope_surface_area in raw_scores
    first = added[0]
    assert "stability_score" in first.raw_scores
    assert "paratope_surface_area" in first.raw_scores


@pytest.mark.asyncio
async def test_normalized_fields_parsed(sample_csv_bytes: bytes) -> None:
    added: list = []
    db = _make_db(added)
    await import_candidates_from_csv(sample_csv_bytes, uuid.uuid4(), db)

    nb001 = next(c for c in added if c.sequence_id == "NB-001")
    assert abs(nb001.binding_affinity_kd - 2.1) < 1e-6
    assert abs(nb001.humanness_score - 0.87) < 1e-6
    assert abs(nb001.aggregation_propensity - 0.12) < 1e-6


# ---------------------------------------------------------------------------
# Missing required columns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_sequence_id_column_raises() -> None:
    csv = b"sequence,Kd (nM)\nABCDE,1.0\n"
    db = _make_db([])
    with pytest.raises(ImportError, match="sequence_id"):
        await import_candidates_from_csv(csv, uuid.uuid4(), db)


@pytest.mark.asyncio
async def test_missing_fasta_column_raises() -> None:
    csv = b"sequence_id,Kd (nM)\nNB-001,1.0\n"
    db = _make_db([])
    with pytest.raises(ImportError, match="fasta_sequence"):
        await import_candidates_from_csv(csv, uuid.uuid4(), db)


# ---------------------------------------------------------------------------
# Row-level soft failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_with_missing_sequence_id_is_skipped() -> None:
    csv = (
        b"sequence_id,sequence,Kd (nM)\n"
        b"NB-001,ABCDE,1.0\n"
        b",FGHIJ,2.0\n"  # missing sequence_id
    )
    added: list = []
    db = _make_db(added)
    result = await import_candidates_from_csv(csv, uuid.uuid4(), db)
    assert result.rows_imported == 1
    assert result.rows_skipped[0]["reason"] == "missing sequence_id"


@pytest.mark.asyncio
async def test_row_with_missing_fasta_is_skipped() -> None:
    csv = (
        b"sequence_id,sequence,Kd (nM)\n"
        b"NB-001,ABCDE,1.0\n"
        b"NB-002,,2.0\n"  # missing fasta
    )
    added: list = []
    db = _make_db(added)
    result = await import_candidates_from_csv(csv, uuid.uuid4(), db)
    assert result.rows_imported == 1
    assert result.rows_skipped[0]["reason"] == "missing fasta_sequence"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_file_raises() -> None:
    db = _make_db([])
    with pytest.raises(ImportError, match="empty"):
        await import_candidates_from_csv(b"", uuid.uuid4(), db)


@pytest.mark.asyncio
async def test_malformed_float_becomes_none() -> None:
    csv = b"sequence_id,sequence,Kd (nM)\nNB-001,ABCDE,not_a_number\n"
    added: list = []
    db = _make_db(added)
    result = await import_candidates_from_csv(csv, uuid.uuid4(), db)
    assert result.rows_imported == 1
    assert added[0].binding_affinity_kd is None


@pytest.mark.asyncio
async def test_optional_score_columns_absent() -> None:
    """File with only required columns — no score columns — should import fine."""
    csv = b"sequence_id,sequence\nNB-001,ABCDEFGH\n"
    added: list = []
    db = _make_db(added)
    result = await import_candidates_from_csv(csv, uuid.uuid4(), db)
    assert result.rows_imported == 1
    assert added[0].binding_affinity_kd is None
    assert added[0].humanness_score is None
