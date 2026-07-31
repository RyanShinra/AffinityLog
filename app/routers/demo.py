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
templates = Jinja2Templates(directory="app/templates")

# The three antibody-HER2 complexes we feature — all loaded AND with Boltz2 structures on disk.
# Labels below are grounded in sequence identity (the antibody_hash), not guesswork: the design and
# humanized lines are the SAME parent trastuzumab, forked at the humanization step (their H+L chains
# were mutated), then each folded against HER2 in a later run.
FEATURED: list[str] = [
    "05f95b5a8caa4a85a2f9a8513817a0c5",  # independent de novo nanobody -> HER2   (H/T)     ipTM 0.40
    "449d46884cbc4249ab5e9ab4706fc890",  # design line -> HER2  (H/L/T) ipTM 0.79  == Round 6 a6d1540b
    "8981aae116524c7683f6e3e310f32e43",  # humanized line -> HER2 (H/L/T) ipTM 0.70 == Round 6 15bad6f9
]                                        #   (…where its OASis humanness 0.24 lives — different row)

_STRUCTURES_DIR = Path("experiment_results")
_CANDIDATE_ID = re.compile(r"^[0-9a-f]{32}$")  # a Bio Discovery candidate id — nothing else may open a file


def _epitope_ints(raw: str | None) -> list[int]:
    """Split the raw ``"87;89;90;…"`` epitope string into residue numbers for the 3D viewer.

    These are HER2 (chain T) residue numbers in the Boltz2 PDB's own numbering — the viewer selects
    ``chain T and resi <these>`` and paints them red. Non-numeric tokens are dropped defensively.
    """
    if not raw:
        return []
    return [int(tok) for tok in raw.split(";") if tok.strip().isdigit()]


@router.get("/demo", response_class=HTMLResponse)
async def demo_page(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> HTMLResponse:
    """Render the demo page from the featured rows of the candidate_summary view (typed, no raw SQL)."""
    result = await session.execute(
        select(CandidateSummary).where(CandidateSummary.candidate_id.in_(FEATURED))
    )
    by_id = {row.candidate_id: row for row in result.scalars().all()}
    candidates = [by_id[cid] for cid in FEATURED if cid in by_id]  # preserve the FEATURED order

    # A JSON-friendly payload the browser's 3Dmol code consumes: where to fetch each structure, and
    # which chain-T residues to highlight. Built server-side so the template stays logic-free.
    viewers = [
        {
            "candidate_id": c.candidate_id,
            "label": c.candidate,
            "experiment": c.experiment,
            "chains": c.chains,
            "pdb_url": f"/demo/pdb/{c.candidate_id}",
            "epitope": _epitope_ints(c.epitope_list),
        }
        for c in candidates
    ]

    # Provenance: pull every row sharing an antibody fingerprint with a featured complex. Where a
    # molecule appears in >1 experiment, its scores are split across those rows (humanness on one,
    # structure on another) — the same-hash grouping reunites them, complementary NULLs and all.
    featured_hashes = {c.antibody_hash for c in candidates if c.antibody_hash}
    lineages: dict[str, list[dict[str, object]]] = {}
    if featured_hashes:
        kin = await session.execute(
            select(CandidateSummary)
            .where(CandidateSummary.antibody_hash.in_(featured_hashes))
            .order_by(CandidateSummary.experiment)
        )
        featured_ids = set(FEATURED)
        for row in kin.scalars().all():
            lineages.setdefault(row.antibody_hash, []).append(
                {
                    "experiment": row.experiment,
                    "candidate": row.candidate,
                    "chains": row.chains,
                    "interface_kind": row.interface_kind,
                    "iptm": row.iptm,
                    "humanness_oasis": row.humanness_oasis,
                    "is_featured": row.candidate_id in featured_ids,
                }
            )
        # Only a fingerprint shared by >1 row tells a cross-experiment story; drop the singletons.
        lineages = {h: rows for h, rows in lineages.items() if len(rows) > 1}

    return templates.TemplateResponse(
        request, "demo.html", {"candidates": candidates, "viewers": viewers, "lineages": lineages}
    )


@router.get("/demo/pdb/{candidate_id}", response_class=PlainTextResponse)
async def demo_pdb(candidate_id: str) -> PlainTextResponse:
    """Return a candidate's PDB text — with the untrusted id validated before any filesystem access."""
    # 1) Only a 32-hex candidate id may proceed — no traversal, no wildcards, nothing else.
    if not _CANDIDATE_ID.match(candidate_id):
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
