"""Column name normalization for Bio Discovery CSV exports.

Different Bio Discovery recipes emit different column headers for the same underlying
metric. This module maps known variants to their canonical normalized field name.
When a real export lands, add new variants here — that should be the only required change.
"""

import re

# Maps normalized field name -> list of known CSV header variants (case-insensitive, stripped)
COLUMN_ALIASES: dict[str, list[str]] = {
    "sequence_id": [
        "sequence_id",
        "seq_id",
        "candidate_id",
        "id",
        "name",
        "sequence name",
    ],
    "fasta_sequence": [
        "fasta_sequence",
        "sequence",
        "fasta",
        "aa_sequence",
        "amino_acid_sequence",
        "protein_sequence",
    ],
    "binding_affinity_kd": [
        "binding_affinity_kd",
        "kd (nm)",
        "kd(nm)",
        "kd",
        "binding_affinity",
        "affinity_kd",
        "affinity (nm)",
        "dissociation_constant",
        "kd_nm",
    ],
    "humanness_score": [
        "humanness_score",
        "humanness",
        "human_score",
        "biophi_score",
        "oasis_score",
        "oasis_identity",
        "humanization_score",
    ],
    "aggregation_propensity": [
        "aggregation_propensity",
        "aggregation",
        "aggrescan_score",
        "agg_score",
        "aggregation_score",
        "aggregation_risk",
    ],
}

_ALIAS_LOOKUP: dict[str, str] = {}
for _field, _variants in COLUMN_ALIASES.items():
    for _v in _variants:
        _ALIAS_LOOKUP[_v.lower().strip()] = _field


def normalize_column(raw_header: str) -> str | None:
    """Return the canonical field name for a CSV header, or None if unrecognized."""
    key = re.sub(r"\s+", " ", raw_header.lower().strip())
    return _ALIAS_LOOKUP.get(key)


REQUIRED_FIELDS = {"sequence_id", "fasta_sequence"}
