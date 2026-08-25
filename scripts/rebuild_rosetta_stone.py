#!/usr/bin/env python3
"""Regenerate the ESM2 property "Rosetta Stone" from saved console HTML — and self-check it.

THE POINT
---------
The ESM2 Property Predictor export emits a *generic* ``predicted_property`` column: five
sub-experiments (one per property) all report into the same column name, so the CSV alone
cannot tell you which candidate was scored for solubility vs hydrophobicity vs … (see
docs/schema-stress-log.md). That mapping lives ONLY in each sub-experiment's console page.
It was originally recovered by hand — clicking into all ten sub-experiments and transcribing
candidate↔property into ``experiment_results/file id mapping.xlsx``.

This script rebuilds that mapping automatically from browser "Save page as → Webpage,
Complete" captures, and — the important part — **validates it against the hand-built xlsx**,
so the reconstruction is proven correct rather than merely plausible. On the captured corpus
it reproduces all 10 rows exactly.

    python scripts/rebuild_rosetta_stone.py "HTML Extracts"

Pipeline:  sub-experiment Overview HTML  --scrape()-->  property
           sub-experiment Results  HTML  --regex----->  candidate id
           compose                                 -->  candidate -> property
           (optional) compare against file id mapping.xlsx

The one remaining MANUAL step is capturing the HTML itself (the ctrl-S dance per page). In a
production setting that is a headless-browser / browser-automation loop — the only piece of
this provenance-recovery pipeline that isn't yet automated.
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

# The repo root, not `scripts/` — so the sibling below is imported as `scripts.<name>` and cannot
# also be loaded as a bare top-level module. `scripts/` has an `__init__.py`, so a file reached both
# ways becomes TWO module objects with two copies of every class, and `isinstance` across them is
# False. That split is also what made a root-level `mypy .` refuse to run before the package marker
# existed. Needed because `scripts` is deliberately NOT installed — `[tool.setuptools.packages.find]`
# is `include = ["app*"]` — so running this file directly puts `scripts/` on the path, not the root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.scrape_input_parameters import scrape  # noqa: E402

# The two ESM2 runs and their five per-property sub-experiments (captured as "<run>_<n> ov/res.html").
_RUNS = ["ESM2 Prediction", "ESM2 Evolved"]
_SUBEXPERIMENTS = range(1, 6)
_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _property_of(overview_html: Path) -> str | None:
    """The single 'Fine-tuned Regression Model' value from a sub-experiment's Overview page."""
    for param in scrape(overview_html)["input_parameters"]:
        if "Regression Model" in param["name"]:
            return param["value"]
    return None


def _candidate_of(results_html: Path) -> str | None:
    """The candidate id from a single-candidate Results page ('Select <32-hex>' aria-label)."""
    hits = re.findall(r"Select ([0-9a-f]{32})", results_html.read_text(encoding="utf-8"))
    return hits[0] if hits else None


def scrape_property_map(extracts_dir: Path) -> dict[str, str]:
    """candidate id -> property, composed across all captured ESM2 sub-experiment pages."""
    mapping: dict[str, str] = {}
    for run in _RUNS:
        for n in _SUBEXPERIMENTS:
            prop = _property_of(extracts_dir / f"{run}_{n} ov.html")
            cand = _candidate_of(extracts_dir / f"{run}_{n} res.html")
            if prop and cand:
                mapping[cand] = prop
    return mapping


def xlsx_property_map(xlsx_path: Path) -> dict[str, str]:
    """candidate id -> property, from the hand-built spreadsheet (Candidate ID col, Sub-exp name col)."""
    zf = zipfile.ZipFile(xlsx_path)
    shared = [
        "".join(t.text or "" for t in si.iter(f"{_XLSX_NS}t"))
        for si in ET.fromstring(zf.read("xl/sharedStrings.xml")).findall(f"{_XLSX_NS}si")
    ]
    out: dict[str, str] = {}
    for row in ET.fromstring(zf.read("xl/worksheets/sheet1.xml")).iter(f"{_XLSX_NS}row"):
        cells = []
        for c in row.findall(f"{_XLSX_NS}c"):
            v = c.find(f"{_XLSX_NS}v")
            shared_index = v.text if c.get("t") == "s" and v is not None else None
            cells.append(shared[int(shared_index)] if shared_index else "")
        if len(cells) >= 6 and re.fullmatch(r"[0-9a-f]{32}", cells[3] or ""):
            out[cells[3]] = cells[5]
    return out


def main() -> None:
    extracts = Path(sys.argv[1] if len(sys.argv) > 1 else "HTML Extracts")
    scraped = scrape_property_map(extracts)
    print(f"Regenerated {len(scraped)} candidate -> property links from saved HTML.\n")

    xlsx = extracts.parent / "experiment_results" / "file id mapping.xlsx"
    truth = xlsx_property_map(xlsx) if xlsx.exists() else {}
    ok = 0
    for cand, prop in sorted(scraped.items(), key=lambda kv: kv[1]):
        expected = truth.get(cand)
        verdict = "" if not truth else ("  OK" if expected == prop else f"  MISMATCH (xlsx={expected})")
        ok += expected == prop
        print(f"  {cand[:8]}  {prop:22}{verdict}")
    if truth:
        print(f"\n{ok}/{len(scraped)} match the hand-built Rosetta Stone (file id mapping.xlsx).")


if __name__ == "__main__":
    main()
