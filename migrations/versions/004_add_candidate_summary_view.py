"""add candidate_summary view

Revision ID: 004
Revises: 003
Create Date: 2026-07-31 17:45:00.000000

A VIEW is DDL, so it belongs in the schema lifecycle rather than in a file a human has to
remember to pipe into psql. Before this migration, a fresh clone running `docker compose up`
got a 500 on /demo because nothing created `candidate_summary`.

**Two-sources-of-truth note.** The SQL below is a *snapshot*, deliberately duplicated from
``sql/candidate_summary.sql``. It is not read from that file at runtime: migrations must be
immutable, and sourcing the file would make this migration's behaviour change retroactively
every time someone edited it. The `.sql` file remains the annotated, iterate-in-TablePlus copy;
this is the version that actually gets applied. `scripts/check_view_migration.py` (run in CI)
fails the build if the file changes without a new migration, which is what keeps them honest.

"""

from collections.abc import Sequence
from typing import Final

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: str | Sequence[str] | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIEW_NAME: Final[str] = "candidate_summary"

# Snapshot of sql/candidate_summary.sql as of revision 004. Comments are kept: this view is a
# teaching artifact as much as a query, and `\d+ candidate_summary` in psql will show the body.
CREATE_VIEW: Final[str] = """
CREATE VIEW candidate_summary AS
SELECT
    e.name                  AS experiment,          -- which run produced this candidate
    c.sequence_id           AS candidate_id,        -- full id (PK for the read-only model; locates the PDB)
    left(c.sequence_id, 8)  AS candidate,           -- short id, easier to read in the console

    -- A candidate has 0..N chains (candidate_chains): HEAVY and LIGHT are the two chains of the
    -- antibody itself, TARGET is the antigen it was folded against (HER2 here).
    string_agg(DISTINCT cc.chain::text, '/' ORDER BY cc.chain::text) AS chains,

    -- Fingerprint of the ANTIBODY (heavy+light only, target excluded): the same binder folded
    -- with or without a target gets the SAME hash, so "one molecule, two rows across
    -- experiments" is visible directly. Two rows sharing this value ARE the same antibody.
    left(md5(string_agg(cc.sequence, '|' ORDER BY cc.chain::text)
             FILTER (WHERE cc.chain::text IN ('HEAVY', 'LIGHT'))), 12) AS antibody_hash,

    -- What does this row's ipTM actually MEASURE? It depends entirely on which chains went into
    -- the fold, so classify the group rather than trusting the column name. The count() guard is
    -- first because this is a LEFT JOIN: bool_or over zero rows returns NULL, not false, so a
    -- chainless candidate would otherwise fall through to the ELSE and be mislabelled.
    CASE
        WHEN count(cc.id) = 0                    THEN 'no chains recorded'
        WHEN bool_or(cc.chain::text = 'TARGET')  THEN 'antibody-target complex'
        WHEN bool_or(cc.chain::text = 'LIGHT')   THEN 'antibody only (H/L pairing)'
        ELSE 'single chain (no interface)'
    END AS interface_kind,

    -- How many metrics live in this candidate's JSONB bag (varies by recipe).
    (SELECT count(*) FROM jsonb_object_keys(c.scores)) AS n_scores,

    -- `->>` extracts a value from the JSONB bag BY KEY, as text. If the key is absent it returns
    -- NULL and the whole expression is NULL — the "this recipe didn't emit this metric" signal.
    -- ipTM = "interface predicted TM-score", 0-1: confidence in how the chains are POSITIONED
    -- relative to each other. Not an affinity (Kd), and assembly-wide, so it only compares
    -- fairly between rows sharing an interface_kind.
    round((c.scores->>'boltz2.protein_iptm')::numeric, 3)              AS iptm,
    -- pLDDT = "predicted Local Distance Difference Test", 0-1: confidence in the FOLD itself.
    round((c.scores->>'boltz2.complex_plddt')::numeric, 3)             AS complex_plddt,
    -- OASis = BioPhi humanness percentile against the Observed Antibody Space repertoires.
    round((c.scores->>'biophi.OASis Percentile_After.H')::numeric, 3)  AS humanness_oasis,
    -- Humatch's CNN humanness call on the heavy chain — a second, independent opinion.
    round((c.scores->>'humatchclassify.CNN_H')::numeric, 3)            AS humatch_human,
    -- TemStaPro thermostability class (mesophilic/thermophilic) — categorical, keep as text.
    c.scores->>'temstapro.thermophilicity.H'                           AS thermo_class,
    (c.scores->>'structure_analysis_boltz2.num_epitope_residues')::int AS epitope_residues,
    -- The epitope residue LIST (semicolon-delimited, in the Boltz2 PDB's own numbering) — the
    -- 3D viewer paints these on chain T. Kept as raw text; the API splits it.
    c.scores->>'structure_analysis_boltz2.epitope_residues'            AS epitope_list

FROM candidates c
JOIN      experiments e       ON e.id = c.experiment_id    -- inner: every candidate has a run
LEFT JOIN candidate_chains cc ON cc.candidate_id = c.id    -- left: keep candidates lacking chains

-- Grouping by c.id (its PK) lets Postgres treat c.scores as functionally dependent, so the JSONB
-- extractions above don't each need listing in GROUP BY. e.name is grouped explicitly.
GROUP BY c.id, e.name
ORDER BY experiment, candidate
"""


def upgrade() -> None:
    """Create the candidate_summary view."""
    op.execute(f"DROP VIEW IF EXISTS {VIEW_NAME}")
    op.execute(CREATE_VIEW)


def downgrade() -> None:
    """Drop the candidate_summary view."""
    op.execute(f"DROP VIEW IF EXISTS {VIEW_NAME}")
