-- candidate_summary — a human-readable window onto the flexible (catalog + JSONB) schema.
--
-- The point: named metrics can be pulled OUT of the untyped `scores` JSONB bag on demand,
-- one row per candidate. NULLs are a feature here — they show which metrics a given recipe
-- did (or didn't) emit, i.e. the flexibility made visible.
--
-- Apply:  docker exec -i affinitylog-db-1 psql -U affinitylog -d affinitylog < sql/candidate_summary.sql
-- Use:    SELECT * FROM candidate_summary;

-- DROP first: CREATE OR REPLACE VIEW can only append columns, not reorder/insert one mid-list.
DROP VIEW IF EXISTS candidate_summary;
CREATE VIEW candidate_summary AS
SELECT
    e.name                  AS experiment,          -- which run produced this candidate
    c.sequence_id           AS candidate_id,        -- full id (PK for the read-only model; used to locate the PDB)
    left(c.sequence_id, 8)  AS candidate,           -- short id, easier to read in the console

    -- A candidate has 0..N chains (candidate_chains). string_agg collapses those rows into
    -- one "H/L/T" string; DISTINCT + ORDER keep it stable ("H/L/T", not "T/H/L").
    string_agg(DISTINCT cc.chain::text, '/' ORDER BY cc.chain::text) AS chains,

    -- Fingerprint of the ANTIBODY (heavy+light only, target excluded): the same binder folded with
    -- or without a HER2 target gets the SAME hash — so "one molecule, two rows across experiments"
    -- is visible right here. Two rows sharing this value ARE the same antibody. (12 hex chars is
    -- plenty to eyeball; the empty cell means an antibody with no H/L, i.e. a target-only row.)
    left(md5(string_agg(cc.sequence, '|' ORDER BY cc.chain::text)
             FILTER (WHERE cc.chain::text IN ('HEAVY', 'LIGHT'))), 12) AS antibody_hash,

    -- How many metrics live in this candidate's JSONB bag (varies by recipe).
    (SELECT count(*) FROM jsonb_object_keys(c.scores)) AS n_scores,

    -- `->>` extracts a value from the JSONB bag BY KEY, as text. ::numeric casts it; round() tidies.
    -- If the key is absent, `->>` returns NULL and the whole expression is NULL — that's the
    -- "this recipe didn't emit this metric" signal, surfaced as an empty cell.
    round((c.scores->>'boltz2.protein_iptm')::numeric, 3)              AS binding_iptm,
    round((c.scores->>'boltz2.complex_plddt')::numeric, 3)             AS complex_plddt,
    round((c.scores->>'biophi.OASis Percentile_After.H')::numeric, 3)  AS humanness_oasis,
    round((c.scores->>'humatchclassify.CNN_H')::numeric, 3)            AS humatch_human,
    c.scores->>'temstapro.thermophilicity.H'                           AS thermo_class,       -- categorical, keep as text
    (c.scores->>'structure_analysis_boltz2.num_epitope_residues')::int AS epitope_residues,
    -- The epitope residue LIST (semicolon-delimited HER2 residue numbers, in the Boltz2 PDB's own
    -- numbering) — the 3D viewer paints these on chain T. Kept as raw text; the API splits it.
    c.scores->>'structure_analysis_boltz2.epitope_residues'            AS epitope_list

FROM candidates c
JOIN      experiments e      ON e.id = c.experiment_id     -- inner: every candidate has a run
LEFT JOIN candidate_chains cc ON cc.candidate_id = c.id    -- left: keep the candidate even if chains are missing

-- string_agg is an aggregate, so we collapse to one row per candidate. Grouping by c.id (its PK)
-- lets Postgres treat c.scores as functionally dependent — so we can read the JSONB above without
-- listing every extraction in GROUP BY. e.name is grouped explicitly.
GROUP BY c.id, e.name
ORDER BY experiment, candidate;
