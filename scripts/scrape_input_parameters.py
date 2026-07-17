#!/usr/bin/env python3
"""Reverse-engineer a Bio Discovery experiment's Input Parameters from a saved HTML page.

WHY THIS EXISTS (the "clever AI" proof-of-concept)
--------------------------------------------------
The CSV export omits a run's *configuration* — models, seeds, hyperparameters, the
per-sub-experiment property choices — so all night we transcribed it by hand (see
``experiment_results/file id mapping.xlsx``, the "Rosetta Stone"). This script is the
tool that makes the hand-transcription unnecessary: point it at a browser "Save page as
→ Webpage, Complete" capture of an experiment's **Overview** page, and it walks the
(deeply nested, React-rendered) DOM and emits the Input Parameters table as clean JSON —
the exact provenance ``Experiment.params`` needs.

It is a demonstration, not a product: read-only parsing of the operator's own saved pages,
no live scraping, no distribution. (ToS review 2026-07-01 cleared read-only access to your
own authenticated account's displayed data.)

    python scripts/scrape_input_parameters.py "HTML Extracts/Her2 Boltz2 Evolved Overview.html"

The table structure is stable: an "Input parameters" heading, then the 5 column headers
(Name / Value / Description / Module / Parameter configuration), then N rows of 5 cells.
A Boltz2-Solo run has one row; an ESM2 or EvoProtGrad run has many — same parser, no changes.
"""

from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

_PARAM_HEADERS = ["Name", "Value", "Description", "Module", "Parameter configuration"]
# Visible text that marks the end of the parameters table (unrelated page chrome / next section).
_TABLE_STOP = frozenset(
    {"Train Model", "Import custom module", "Request BYOM Access", "Overview", "Wet lab orders"}
)


class _VisibleText(HTMLParser):
    """Collect visible text nodes, dropping inline <style> noise (CSS rule text)."""

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text and not text.startswith(("#awsccc", ".awsui", ".awsccc", "@media")):
            self.chunks.append(text)


def scrape(html_path: Path) -> dict[str, object]:
    parser = _VisibleText()
    parser.feed(html_path.read_text(encoding="utf-8"))
    text = parser.chunks

    def value_after(label: str) -> str | None:
        for i, chunk in enumerate(text):
            if chunk == label and i + 1 < len(text):
                return text[i + 1]
        return None

    # Experiment name = the breadcrumb entry immediately before "Overview"/"Results".
    name: str | None = None
    for i, chunk in enumerate(text):
        if chunk in ("Overview", "Results") and i > 0:
            name = text[i - 1]
            break

    params: list[dict[str, str]] = []
    if "Input parameters" in text:
        start = text.index("Input parameters") + 1 + len(_PARAM_HEADERS)
        row: list[str] = []
        for chunk in text[start:]:
            if chunk in _TABLE_STOP:
                break
            row.append(chunk)
            if len(row) == len(_PARAM_HEADERS):
                params.append(dict(zip(["name", "value", "description", "module", "config"], row)))
                row = []

    return {"experiment": name, "recipe": value_after("Recipe"), "input_parameters": params}


class _RowMap(HTMLParser):
    """Per <tr>, capture the candidate id (from a 'Select <id>' aria-label) and the
    sub-experiment id (from an experiments/<id>/overview href)."""

    def __init__(self) -> None:
        super().__init__()
        self._in_tr = False
        self._cand: str | None = None
        self._sub: str | None = None
        self.rows: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "tr":
            self._in_tr, self._cand, self._sub = True, None, None
        if self._in_tr:
            m = re.search(r"Select ([0-9a-f]{32})", a.get("aria-label") or "")
            if m:
                self._cand = m.group(1)
            m = re.search(r"/experiments/([0-9a-f]{32})/overview", a.get("href") or "")
            if m:
                self._sub = m.group(1)

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self._in_tr:
            if self._cand and self._sub:
                self.rows.append((self._cand, self._sub))
            self._in_tr = False


def scrape_candidate_map(results_html_path: Path) -> list[dict[str, str]]:
    """Recover candidate -> sub-experiment links from a saved *Results* page.

    Complements ``scrape()``: a swept run's Results table has one row per candidate, each
    carrying its sub-experiment as a link. Composed with the per-sub-experiment ``scrape()``
    (which yields that sub-experiment's swept parameter), this reconstructs the full
    candidate <-> sub-experiment <-> parameter "Rosetta Stone" the CSV export omits.

    Validated 2026-07-17 against Experiment 5: all 4 recovered links matched the mapping
    independently derived from the CSV's ``experimentId`` column.
    """
    parser = _RowMap()
    parser.feed(results_html_path.read_text(encoding="utf-8"))
    return [{"candidate": c, "subexperiment": s} for c, s in dict.fromkeys(parser.rows)]


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} <saved-page.html>   (Overview -> params; Results -> candidate map)")
    path = Path(sys.argv[1])
    if not path.exists():
        sys.exit(f"error: {path} not found")
    # A Results page has the candidate table; an Overview page has the Input Parameters table.
    text = path.read_text(encoding="utf-8")
    if "Input parameters" in text:
        print(json.dumps(scrape(path), indent=2))
    else:
        print(json.dumps(scrape_candidate_map(path), indent=2))


if __name__ == "__main__":
    main()
