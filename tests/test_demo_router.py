"""Demo-router unit tests — no database, no app, no network.

These cover the three pure-ish helpers behind the /demo page. They exist because each one encodes
a decision whose *absence* would be invisible: an unknown interface kind must not vanish from the
page, a stray file must not become a queryable candidate id, and a missing epitope must degrade to
"nothing to paint" rather than an exception. Rendering is verified in the browser; this is the part
that can rot silently.
"""

from pathlib import Path

from app.routers.demo import _KIND_ORDER, _epitope_ints, _kind_rank, structure_ids


class TestKindRank:
    """_kind_rank decides tab-group order, and what happens to a kind nobody anticipated."""

    def test_known_kinds_sort_in_declared_order(self) -> None:
        # Not alphabetical on purpose: complexes lead because they're the only group where ipTM
        # means "does it bind HER2". Sorting the labels alphabetically would put the H/L pairings
        # (whose ~0.95 scores are NOT binding) first, which is the exact misreading to avoid.
        assert sorted(_KIND_ORDER, key=_kind_rank) == _KIND_ORDER
        assert sorted(reversed(_KIND_ORDER), key=_kind_rank) == _KIND_ORDER

    def test_unknown_kind_sorts_last_rather_than_vanishing(self) -> None:
        # The view's CASE has a fourth branch ('no chains recorded') that _KIND_ORDER doesn't list,
        # and future recipes may add more. Ranking unknowns last means a new kind shows up at the
        # bottom of the page instead of being silently dropped from the viewer.
        unknown = "no chains recorded"
        assert unknown not in _KIND_ORDER
        assert _kind_rank(unknown) > max(_kind_rank(k) for k in _KIND_ORDER)

        mixed = [unknown, *_KIND_ORDER]
        assert sorted(mixed, key=_kind_rank) == [*_KIND_ORDER, unknown]

    def test_unknown_kinds_are_ordered_among_themselves(self) -> None:
        # The tie-break on the label keeps the output stable rather than dependent on input order.
        assert sorted(["zeta", "alpha"], key=_kind_rank) == ["alpha", "zeta"]


class TestStructureIds:
    """structure_ids answers 'which candidates can we draw?' by looking at the filesystem."""

    @staticmethod
    def _make(tmp_path: Path, *names: str) -> None:
        run = tmp_path / "experiment_1"
        run.mkdir(exist_ok=True)
        for n in names:
            (run / n).write_text("ATOM", encoding="utf-8")

    def test_finds_valid_ids_and_strips_the_suffix(self, tmp_path: Path, monkeypatch) -> None:
        good = "a" * 32
        other = "0123456789abcdef" * 2
        self._make(tmp_path, f"{good}_boltz2.pdb", f"{other}_boltz2.pdb")
        monkeypatch.setattr("app.routers.demo._STRUCTURES_DIR", tmp_path)
        assert structure_ids() == {good, other}

    def test_rejects_anything_that_is_not_a_candidate_id(self, tmp_path: Path, monkeypatch) -> None:
        # The ids this returns are fed straight into a membership test against database rows, so
        # the filename is treated as untrusted input: wrong length, non-hex, uppercase, a different
        # tool's suffix, or an id with anything appended must all be ignored rather than matched.
        self._make(
            tmp_path,
            "short_boltz2.pdb",
            f"{'g' * 32}_boltz2.pdb",  # non-hex
            f"{'A' * 32}_boltz2.pdb",  # uppercase — the id vocabulary is lowercase hex
            f"{'a' * 33}_boltz2.pdb",  # too long
            f"{'a' * 32}_rfantibody.pdb",  # real file, but not the Boltz2 structure
            f"prefix_{'b' * 32}_boltz2.pdb",  # id must be the whole stem, not a substring
        )
        monkeypatch.setattr("app.routers.demo._STRUCTURES_DIR", tmp_path)
        assert structure_ids() == set()

    def test_missing_directory_is_empty_not_an_error(self, tmp_path: Path, monkeypatch) -> None:
        # A clone without experiment_results/ should render a page with no structures, not a 500.
        monkeypatch.setattr("app.routers.demo._STRUCTURES_DIR", tmp_path / "nope")
        assert structure_ids() == set()


class TestEpitopeInts:
    """_epitope_ints turns the raw ';'-delimited score value into residue numbers for 3Dmol."""

    def test_parses_the_semicolon_list(self) -> None:
        assert _epitope_ints("87;89;90;134") == [87, 89, 90, 134]

    def test_absent_epitope_is_an_empty_list(self) -> None:
        # NULL for every fold with no TARGET chain — 10 of the 14 rows in the corpus. The viewer
        # skips its highlight step on an empty list, which is why target-less folds need no
        # special-casing anywhere downstream.
        assert _epitope_ints(None) == []
        assert _epitope_ints("") == []

    def test_non_numeric_tokens_are_dropped(self) -> None:
        assert _epitope_ints("87;;abc;90") == [87, 90]
