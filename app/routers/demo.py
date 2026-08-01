"""Demo page — the flexible schema, made visible in 3D.

Serves one page that ties the loaded corpus to its Boltz2-predicted structures:
Postgres (``candidate_summary`` view) -> this API -> 3Dmol in the browser, all local.

- ``GET /demo``              -> the page, rendered from a Jinja template with the featured rows.
- ``GET /demo/pdb/{id}``     -> the candidate's structure file, same-origin (so 3Dmol fetches it,
                               no CORS). The ``{id}`` segment is untrusted, so it is validated hard
                               before anything touches the filesystem.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.views import CandidateSummary

router = APIRouter()

# Anchor every path to the repo root derived from THIS file, never the process working directory.
# app/routers/demo.py -> parents[2] is the repo root. Without this, both the templates and the
# structure files resolve against wherever uvicorn happened to be launched from.
_REPO_ROOT = Path(__file__).resolve().parents[2]

templates = Jinja2Templates(directory=str(_REPO_ROOT / "app" / "templates"))

_STRUCTURES_DIR = _REPO_ROOT / "experiment_results"
# A Bio Discovery candidate id — nothing else may open a file. Matched with fullmatch(), not
# match(): `$` would also accept a trailing newline, and this gate guards filesystem access.
_CANDIDATE_ID = re.compile(r"[0-9a-f]{32}")
# Structure filenames are "<32-hex candidate id>_boltz2.pdb". Anchored, so a stray file dropped in
# experiment_results/ can't smuggle an arbitrary id into the query below.
_PDB_NAME = re.compile(r"([0-9a-f]{32})_boltz2\.pdb")

# Display order for the viewer's tab groups. NOT alphabetical, and that's the point: ipTM only means
# "does it bind HER2" for the complexes, so they lead. The other two groups are shown rather than
# hidden because their ipTM values (~0.95 for H/L pairing, 0.000 for a lone chain) are exactly what
# makes the classification worth having — a naive sort by ipTM would rank them above every real
# complex. See the 2026-07-31 entry in docs/schema-stress-log.md.
_KIND_ORDER: list[str] = [
    "antibody-target complex",  # H/L/T or H/T — a real binding interface, ipTM 0.196-0.793
    "antibody only (H/L pairing)",  # H/L, no antigen — ipTM scores the heavy-light pairing, ~0.95
    "single chain (no interface)",  # H alone — nothing to score against, ipTM 0.000
]


def structure_ids() -> set[str]:
    """Candidate ids that have a Boltz2 structure on disk.

    Replaces the old hardcoded FEATURED list: drop a new ``<id>_boltz2.pdb`` into
    ``experiment_results/<anything>/`` and it shows up on the page with no code change.

    Returns a *set* because the only question asked of it is membership ("is this row viewable?").
    Note this is the source of truth for *which structures can be rendered* — it is deliberately
    NOT the source of truth for which candidates exist. The stats table shows all 14 rows from the
    database; only these have a file to draw, and the gap between the two lists is itself
    informative (rows without a structure are the ones that were humanness-scored, never folded).
    """
    return {m.group(1) for p in _STRUCTURES_DIR.glob("*/*_boltz2.pdb") if (m := _PDB_NAME.fullmatch(p.name))}


def _kind_rank(kind: str) -> tuple[int, str]:
    """Sort key placing known interface kinds in _KIND_ORDER, and anything unrecognised last.

    Defensive on purpose: the view's CASE can emit a fourth value ("no chains recorded") that this
    list doesn't mention, and future recipes may add more. Ranking unknowns last means a new kind
    appears at the bottom of the page rather than being silently dropped from the viewer.
    """
    return (_KIND_ORDER.index(kind) if kind in _KIND_ORDER else len(_KIND_ORDER), kind)


def _epitope_ints(raw: str | None) -> list[int]:
    """Split the raw ``"87;89;90;…"`` epitope string into residue numbers for the 3D viewer.

    These are HER2 (chain T) residue numbers in the Boltz2 PDB's own numbering — the viewer selects
    ``chain T and resi <these>`` and paints them red. Non-numeric tokens are dropped defensively.
    """
    if not raw:
        return []
    return [int(tok) for tok in raw.split(";") if tok.strip().isdigit()]


@router.get("/demo", response_class=HTMLResponse)
async def demo_page(request: Request, session: Annotated[AsyncSession, Depends(get_session)]) -> HTMLResponse:
    """Render the demo page: every candidate in the stats table, every *renderable* one in 3D.

    One query, three views of the same rows — the whole corpus for the table, the subset with a
    structure on disk for the viewer, and the fingerprint-sharing subset for the provenance panel.
    Everything below is plain Python over rows already in memory; no further round-trips.
    """
    # No WHERE clause: the stats table deliberately shows the FULL corpus, structures or not. The
    # rows without one are not noise — they're the humanness-scored siblings that were never folded,
    # and their empty ipTM cells next to a populated humanness cell are the flexible schema's
    # complementary-NULL story told at corpus scale.
    #
    # ORDER BY iptm DESC here rather than sorting later: it means every group built below comes out
    # pre-sorted (strongest binder first) with no second sort anywhere. NULLS LAST keeps the
    # never-folded rows at the bottom of the table instead of leading it.
    result = await session.execute(select(CandidateSummary).order_by(CandidateSummary.iptm.desc().nullslast()))
    candidates = list(result.scalars().all())

    # Which of those we can actually draw. Read once per request, not per row — a single directory
    # glob rather than 14 filesystem probes.
    renderable = structure_ids()

    # --- 3D viewer payload, bucketed by interface kind -------------------------------------------
    # NOTE: this is *partitioning*, not SQL aggregation. The view's own GROUP BY already collapsed
    # each candidate's chain rows into one row; these are finished rows being filed into display
    # buckets. Nothing here needs the database.
    #
    # Why grouped at all: ipTM means a different physical quantity per bucket, so presenting the
    # nine structures as one flat list would invite exactly the comparison that is invalid.
    grouped: dict[str, list[dict[str, object]]] = {}
    for c in candidates:
        if c.candidate_id not in renderable:
            continue  # in the table, but there's no file to render
        grouped.setdefault(c.interface_kind, []).append(
            {
                "candidate_id": c.candidate_id,
                "label": c.candidate,
                "experiment": c.experiment,
                "chains": c.chains,
                "iptm": c.iptm,
                "pdb_url": f"/demo/pdb/{c.candidate_id}",
                # Empty for anything without a TARGET chain, which is the correct no-op: the viewer
                # skips its highlight step, so target-less folds need no special-casing downstream.
                "epitope": _epitope_ints(c.epitope_list),
            }
        )

    # A list, not the dict — dict ordering survives `| tojson`, but an explicit ordered list makes
    # the contract obvious to the template and lets _kind_rank own the ordering decision.
    viewer_groups = [{"kind": kind, "items": grouped[kind]} for kind in sorted(grouped, key=_kind_rank)]

    # --- Provenance panel ------------------------------------------------------------------------
    # Same rows again, regrouped by antibody fingerprint. Two rows sharing a hash ARE the same
    # antibody, so a hash appearing under >1 experiment means one molecule whose scores were split
    # across separate CSV exports (humanness on the row where it was humanized, structure on the row
    # where it was folded). Now that the query returns the whole corpus this needs no second query —
    # and it finds every lineage, not just those touching a hand-picked featured list.
    lineages: dict[str, list[dict[str, object]]] = {}
    for c in candidates:
        # `antibody_hash` is NULL only for a row with no H/L chains at all (a target-only row).
        # Such a row has no antibody to trace, so it belongs in no lineage.
        if c.antibody_hash is None:
            continue
        lineages.setdefault(c.antibody_hash, []).append(
            {
                "experiment": c.experiment,
                "candidate": c.candidate,
                "chains": c.chains,
                "interface_kind": c.interface_kind,
                "iptm": c.iptm,
                "humanness_oasis": c.humanness_oasis,
                # Drives the highlight: this row is one you can click a tab for above.
                "is_featured": c.candidate_id in renderable,
            }
        )
    # A fingerprint on a single row tells no cross-experiment story — drop the singletons.
    lineages = {h: rows for h, rows in lineages.items() if len(rows) > 1}

    return templates.TemplateResponse(
        request,
        "demo.html",
        {"candidates": candidates, "viewer_groups": viewer_groups, "lineages": lineages},
    )


@router.get("/demo/pdb/{candidate_id}", response_class=PlainTextResponse)
async def demo_pdb(candidate_id: str) -> PlainTextResponse:
    """Return a candidate's PDB text — with the untrusted id validated before any filesystem access."""
    # 1) Only a 32-hex candidate id may proceed — no traversal, no wildcards, nothing else.
    if not _CANDIDATE_ID.fullmatch(candidate_id):
        raise HTTPException(status_code=400, detail="invalid candidate id")
    # 2) We build the search pattern; the user string never becomes a raw path.
    matches = list(_STRUCTURES_DIR.glob(f"*/{candidate_id}_boltz2.pdb"))
    if not matches:
        raise HTTPException(status_code=404, detail="structure not found")
    # 3) Belt-and-suspenders: confirm the resolved file is still inside the structures dir.
    pdb = matches[0].resolve()
    if _STRUCTURES_DIR.resolve() not in pdb.parents:
        raise HTTPException(status_code=400, detail="path escaped structures directory")
    return PlainTextResponse(pdb.read_text())
