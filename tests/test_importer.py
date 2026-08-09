"""CSV importer tests — no database required.

WHY THIS FILE EXISTS AT ALL
---------------------------
It was a 115-byte tombstone until 2026-08-09, and its absence let a real bug ship: the
``chain`` -> ``role`` rename in migration 007 renamed the mapped attribute on
``CandidateChain``, but ``parse_chains`` kept passing ``chain=``. SQLAlchemy's declarative
constructor rejects unknown kwargs, so every import raised
``TypeError: 'chain' is an invalid keyword argument``. CSV upload is the *only* path data
takes into this project, and nothing caught it — ruff and mypy do not check kwargs against a
generated constructor, and ``scripts/check_model_drift.py`` compares the ORM to the database,
not to its callers. A five-agent code review found it. One test would have.

WHY NO DATABASE
---------------
``build_scores``, ``parse_chains`` and ``group_rows_by_experiment`` are pure. ``load_csv`` is
``async`` but never awaits the session — it only calls ``session.add()``, which puts objects in
the identity map without connecting. So the whole module, orchestration included, can be
exercised against a session that has no server behind it. Verified with the container stopped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.database import AsyncSessionLocal
from app.importer.csv_importer import (
    build_scores,
    group_rows_by_experiment,
    load_csv,
    parse_chains,
)
from app.models.orm import ChainRole


class TestParseChains:
    """Splits the ``/``-joined sequence into one CandidateChain per chain."""

    def test_builds_a_chain_per_label(self) -> None:
        # THE REGRESSION TEST. This constructs CandidateChain, so it fails outright if the
        # keyword ever drifts from the mapped attribute name again.
        chains = parse_chains({"id": "abc", "sequenceType": "H/L/T", "sequence": "QVQ/DIQ/HER"})
        assert [(c.role, c.sequence) for c in chains] == [
            (ChainRole.HEAVY, "QVQ"),
            (ChainRole.LIGHT, "DIQ"),
            (ChainRole.TARGET, "HER"),
        ]

    def test_repeated_labels_get_distinct_ordinals(self) -> None:
        # A two-target fold is legal, so the label alone cannot identify a chain. Ordinals are
        # counted PER LABEL, not per row: the heavy chain stays 0 while the targets go 0, 1.
        chains = parse_chains({"id": "x", "sequenceType": "H/T/T", "sequence": "QVQ/AAA/BBB"})
        assert [(c.role, c.ordinal) for c in chains] == [
            (ChainRole.HEAVY, 0),
            (ChainRole.TARGET, 0),
            (ChainRole.TARGET, 1),
        ]

    def test_label_and_sequence_count_must_agree(self) -> None:
        # "Refusing to guess" is the documented contract — a silent zip would drop a chain and
        # produce a candidate that looks complete.
        with pytest.raises(ValueError, match="refusing to guess"):
            parse_chains({"id": "x", "sequenceType": "H/L", "sequence": "QVQ"})

    def test_missing_labels_is_an_error_not_an_empty_list(self) -> None:
        with pytest.raises(ValueError, match="no labels in sequenceType"):
            parse_chains({"id": "x", "sequenceType": "", "sequence": "QVQ"})

    def test_whitespace_around_labels_and_parts_is_tolerated(self) -> None:
        chains = parse_chains({"id": "x", "sequenceType": " H / T ", "sequence": " QVQ / HER "})
        assert [(c.role, c.sequence) for c in chains] == [(ChainRole.HEAVY, "QVQ"), (ChainRole.TARGET, "HER")]


class TestBuildScores:
    """Turns one CSV row into the ``scores`` JSONB bag."""

    def test_structural_columns_are_excluded(self) -> None:
        row = {"id": "abc", "sequence": "QVQ", "sequenceType": "H", "experimentId": "e1", "boltz2.ptm": "0.93"}
        assert build_scores(row) == {"boltz2.ptm": "0.93"}

    def test_empty_cells_are_dropped_rather_than_stored_as_empty_string(self) -> None:
        # "This module did not run on this sequence" is not the same as a measured value, and the
        # complementary NULLs are the flexible schema's whole story — see the /demo stats table.
        assert build_scores({"id": "a", "boltz2.ptm": "", "biophi.OASis Percentile_After.H": "0.5"}) == {
            "biophi.OASis Percentile_After.H": "0.5"
        }

    def test_values_are_kept_as_raw_uncoerced_strings(self) -> None:
        # Real exports contain "<40", "-", semicolon lists and embedded JSON. Coercing here would
        # decide the type before the catalog gets a say; seed_metric_skeleton.py infers value_type
        # from these strings later.
        row = {"id": "a", "m.censored": "<40", "m.absent": "-", "m.list": "87;89;90", "m.number": "0.93"}
        assert build_scores(row) == {
            "m.censored": "<40",
            "m.absent": "-",
            "m.list": "87;89;90",
            "m.number": "0.93",
        }

    def test_the_full_header_is_the_key_spaces_and_all(self) -> None:
        # JSONB keys are just strings, so the header goes in verbatim — no normalising, because
        # the catalog's identity is derived from exactly this text.
        assert build_scores({"id": "a", "biophi.OASis Percentile_After.H": "0.5"}) == {"biophi.OASis Percentile_After.H": "0.5"}


class TestGroupRowsByExperiment:
    """A combined export of a swept run carries one experimentId per subexperiment."""

    def test_rows_are_grouped_by_experiment_id(self) -> None:
        rows = [
            {"id": "a", "experimentId": "e1"},
            {"id": "b", "experimentId": "e2"},
            {"id": "c", "experimentId": "e1"},
        ]
        grouped = group_rows_by_experiment(rows)
        assert set(grouped) == {"e1", "e2"}
        assert [r["id"] for r in grouped["e1"]] == ["a", "c"]

    def test_absent_column_means_one_experiment_not_unknown(self) -> None:
        # None is a real answer here — "this whole CSV is one experiment" — which is why the
        # docstring says it does not mean "unknown experiment".
        grouped = group_rows_by_experiment([{"id": "a"}, {"id": "b"}])
        assert list(grouped) == [None]
        assert len(grouped[None]) == 2

    def test_blank_experiment_id_is_treated_as_absent(self) -> None:
        assert list(group_rows_by_experiment([{"id": "a", "experimentId": "   "}])) == [None]


class TestLoadCsv:
    """End to end over a real file, with no server behind the session."""

    @staticmethod
    def _write(tmp_path: Path, text: str) -> Path:
        path = tmp_path / "export.csv"
        path.write_text(text, encoding="utf-8")
        return path

    async def test_loads_one_experiment_with_chains_and_scores(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "id,sequence,sequenceType,boltz2.ptm,biophi.x\nabc,QVQ/HER,H/T,0.93,\n",
        )
        async with AsyncSessionLocal() as session:
            experiments = await load_csv(session, path, name="demo run")

        assert len(experiments) == 1
        experiment = experiments[0]
        assert experiment.name == "demo run"  # no experimentId column, so the name is not suffixed
        assert experiment.source_filename == "export.csv"

        candidate = experiment.candidates[0]
        assert candidate.sequence_id == "abc"
        assert candidate.scores == {"boltz2.ptm": "0.93"}  # the empty biophi.x cell is gone
        assert [(c.role, c.ordinal) for c in candidate.chains] == [(ChainRole.HEAVY, 0), (ChainRole.TARGET, 0)]

    async def test_a_swept_export_becomes_one_experiment_per_subexperiment(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "id,sequence,sequenceType,experimentId,boltz2.ptm\n"
            "a,QVQ,H,abcdef1234,0.9\n"
            "b,DIQ,H,abcdef1234,0.8\n"
            "c,EVQ,H,99887766ff,0.7\n",
        )
        async with AsyncSessionLocal() as session:
            experiments = await load_csv(session, path, name="sweep")

        assert len(experiments) == 2
        # The name carries the first 8 characters of the id, which is what makes two arms of one
        # sweep distinguishable in the demo table.
        assert {e.name for e in experiments} == {"sweep [abcdef12]", "sweep [99887766]"}
        assert {len(e.candidates) for e in experiments} == {2, 1}

    async def test_params_are_stamped_on_every_experiment(self, tmp_path: Path) -> None:
        # params is operator-transcribed provenance the export cannot carry.
        path = self._write(tmp_path, "id,sequence,sequenceType,experimentId,m.x\na,QVQ,H,e1,1\n")
        async with AsyncSessionLocal() as session:
            experiments = await load_csv(session, path, name="run", params={"model": "esm"})

        assert experiments[0].params == {"model": "esm", "experimentId": "e1"}

    async def test_a_file_with_no_data_rows_is_rejected(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "id,sequence,sequenceType\n")
        async with AsyncSessionLocal() as session:
            with pytest.raises(ValueError, match="no data rows"):
                await load_csv(session, path, name="empty")

    async def test_missing_structural_columns_are_named_in_the_error(self, tmp_path: Path) -> None:
        # experimentId is legitimately optional, so it must NOT appear in the complaint.
        path = self._write(tmp_path, "id,boltz2.ptm\nabc,0.93\n")
        async with AsyncSessionLocal() as session:
            with pytest.raises(ValueError, match=r"missing required column\(s\)") as excinfo:
                await load_csv(session, path, name="broken")

        message = str(excinfo.value)
        assert "sequence" in message and "sequenceType" in message
        assert "experimentId" not in message
