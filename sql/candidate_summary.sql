-- candidate_summary — a human-readable window onto the flexible (catalog + JSONB) schema.
--
-- The point: named metrics can be pulled OUT of the untyped `scores` JSONB bag on demand,
-- one row per candidate. NULLs are a feature here — they show which metrics a given recipe
-- did (or didn't) emit, i.e. the flexibility made visible.
--
-- Apply:  docker exec -i affinitylog-db-1 psql -U affinitylog -d affinitylog < sql/candidate_summary.sql
-- Use:    SELECT * FROM candidate_summary;
--
-- ⚠ THIS FILE IS THE ITERATION COPY, NOT WHAT SHIPS.
-- The version that actually gets applied lives inline in
-- migrations/versions/004_add_candidate_summary_view.py — a migration must be an immutable
-- snapshot, so it can't just read this file. Iterate here freely (re-apply with the command
-- above, paste into TablePlus, etc.), but **before opening a PR, if you changed the view's
-- columns, cut a new migration** with the updated SQL copied in.
--     scripts/check_view_migration.py  (also run in CI) fails the build if the two disagree.
--     It compares column lists only, so re-wording these comments is free.

-- DROP first: CREATE OR REPLACE VIEW can only append columns, not reorder/insert one mid-list.
DROP VIEW IF EXISTS candidate_summary;
CREATE VIEW candidate_summary AS
SELECT
    e.name                  AS experiment,          -- which run produced this candidate
    c.sequence_id           AS candidate_id,        -- full id (PK for the read-only model; used to locate the PDB)
    left(c.sequence_id, 8)  AS candidate,           -- short id, easier to read in the console

    -- A candidate has 0..N chains (candidate_chains): HEAVY and LIGHT are the two chains of the
    -- antibody itself, TARGET is the antigen it was folded against (HER2 here). string_agg
    -- collapses those rows into one string; DISTINCT + ORDER keep it stable (never "TARGET/HEAVY").
    -- The alias stays `chains` deliberately even though it aggregates `role`: as a display column it
    -- answers "which chains does this candidate have", and the roles are what identify them.
    string_agg(DISTINCT cc.role::text, '/' ORDER BY cc.role::text) AS chains,

    -- Fingerprint of the ANTIBODY (heavy+light only, target excluded): the same binder folded with
    -- or without a HER2 target gets the SAME hash — so "one molecule, two rows across experiments"
    -- is visible right here. Two rows sharing this value ARE the same antibody. (12 hex chars is
    -- plenty to eyeball; the empty cell means an antibody with no H/L, i.e. a target-only row.)
    left(md5(string_agg(cc.sequence, '|' ORDER BY cc.role::text)
             FILTER (WHERE cc.role::text IN ('HEAVY', 'LIGHT'))), 12) AS antibody_hash,

    -- What does this row's ipTM actually MEASURE? It depends entirely on which chains went into the
    -- fold, so we classify the group instead of trusting the score's key name. bool_or is an aggregate:
    -- "did ANY chain row in this candidate's group match?" — the aggregate-friendly way to ask a
    -- membership question, so it sits alongside the string_agg above without re-deriving it.
    -- Order matters: CASE stops at the first true branch, so the most specific test goes first
    -- (an H/L/T row also has a LIGHT chain, but it's a complex, not a pairing).
    -- The count() guard is first for a subtle reason: this is a LEFT JOIN, so a candidate with NO
    -- chain rows still produces a group — and bool_or over zero rows returns NULL, not false. NULL
    -- isn't true, so such a row would silently fall through to the ELSE and be mislabelled.
    CASE
        WHEN count(cc.id) = 0                    THEN 'no chains recorded'
        WHEN bool_or(cc.role::text = 'TARGET')   THEN 'antibody-target complex'
        WHEN bool_or(cc.role::text = 'LIGHT')    THEN 'antibody only (H/L pairing)'
        ELSE 'single chain (no interface)'
    END AS interface_kind,

    -- How many metrics live in this candidate's JSONB bag (varies by recipe).
    (SELECT count(*) FROM jsonb_object_keys(c.scores)) AS n_scores,

    -- `->>` extracts a value from the JSONB bag BY KEY, as text. ::numeric casts it; round() tidies.
    -- If the key is absent, `->>` returns NULL and the whole expression is NULL — that's the
    -- "this recipe didn't emit this metric" signal, surfaced as an empty cell.
    -- ipTM = "interface predicted TM-score", 0-1: the model's confidence in how the chains are
    -- POSITIONED RELATIVE TO EACH OTHER. Not an affinity (Kd). Read it with interface_kind above:
    -- it is an assembly-wide score, so it only compares fairly between rows of the same kind.
    round((c.scores->>'boltz2.protein_iptm')::numeric, 3)              AS iptm,

    -- pLDDT = "predicted Local Distance Difference Test", 0-1: per-residue confidence in the FOLD
    -- itself (is this shape right?), averaged over the complex. Independent of whether it binds.
    round((c.scores->>'boltz2.complex_plddt')::numeric, 3)             AS complex_plddt,

    -- OASis = BioPhi's humanness percentile: how often this antibody's short peptides actually occur
    -- in the Observed Antibody Space repertoire database. Higher = more human-looking = lower
    -- predicted immunogenicity risk. ".H" = measured on the heavy chain.
    round((c.scores->>'biophi.OASis Percentile_After.H')::numeric, 3)  AS humanness_oasis,

    -- Humatch's CNN (convolutional neural network) humanness call on the heavy chain — a second,
    -- independently-trained opinion alongside OASis.
    round((c.scores->>'humatchclassify.CNN_H')::numeric, 3)            AS humatch_human,

    -- TemStaPro's thermostability class (mesophilic/thermophilic) — categorical, so keep it as text.
    c.scores->>'temstapro.thermophilicity.H'                           AS thermo_class,
    (c.scores->>'structure_analysis_boltz2.num_epitope_residues')::int AS epitope_residues,

    -- The epitope residue LIST (semicolon-delimited HER2 residue numbers, in the Boltz2 PDB's own
    -- numbering) — the 3D viewer paints these on chain T (the PDB's own chain letter, not the role
    -- enum; they correspond here, but they are separate vocabularies). Raw text; the API splits it.
    c.scores->>'structure_analysis_boltz2.epitope_residues'            AS epitope_list

FROM candidates c
JOIN      experiments e      ON e.id = c.experiment_id     -- inner: every candidate has a run
LEFT JOIN candidate_chains cc ON cc.candidate_id = c.id    -- left: keep the candidate even if chains are missing

-- string_agg is an aggregate, so we collapse to one row per candidate. Grouping by c.id (its PK)
-- lets Postgres treat c.scores as functionally dependent — so we can read the JSONB above without
-- listing every extraction in GROUP BY. e.name is grouped explicitly.
GROUP BY c.id, e.name
ORDER BY experiment, candidate;
