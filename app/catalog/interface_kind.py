"""What a candidate's ipTM-style scores are actually measuring.

This is the vocabulary produced by the ``CASE`` in ``sql/candidate_summary.sql`` — the discriminator
behind the finding this project turns on. ``boltz2.protein_iptm`` is one stable JSONB key that means
three physically different things depending on which chains went into the fold:

    antibody-target complex        0.196 - 0.793   real HER2 binding
    antibody only (H/L pairing)    0.948 - 0.962   the antibody's own heavy-light pairing
    single chain (no interface)    0.000           nothing to score against

Three non-overlapping bands, and the *meaningless* ones score highest — so sorting by raw ipTM ranks
target-less folds above every real complex. The discriminator is in neither the key nor the value; it
is derived from the sibling ``candidate_chains`` rows. See the 2026-07-31 entry in
``docs/schema-stress-log.md``.

WHY THIS IS A PLAIN ``enum.Enum`` AND NOT A ``@strawberry.enum``
---------------------------------------------------------------
Strawberry wraps an existing ``Enum``, so the GraphQL layer decorates this rather than redefining it.
Keeping the definition here means the seeding scripts and the tests can use it without importing the
GraphQL layer — and, more to the point, keeps it a single vocabulary rather than one per consumer.

It is deliberately **not** in ``app/models/orm.py``: every enum there is a native Postgres ``ENUM``
type, and this one is not. It is a string computed by a view at query time. Putting it beside the
others would imply an ``ALTER TYPE`` obligation that does not exist.

THE VALUES ARE A CONTRACT, NOT LABELS
-------------------------------------
Each ``value`` below must match its ``CASE`` arm **byte for byte**, because the same strings are also
the ``variant`` column of the nine INTERFACE rows in ``seed/catalog.json`` — that string equality is
how a score key finds the one catalog row of three that explains it. Change a word here, or in the
SQL, or in the seed file, and those three keys stop resolving silently.

``tests/test_interface_kind.py`` is what makes that fail loudly instead. Read it before editing any
of the strings below.
"""

from __future__ import annotations

import enum


class InterfaceKind(enum.Enum):
    """The four arms of the view's ``CASE``, in the order the SQL evaluates them.

    The first three are biology. The fourth is not: ``NO_CHAINS_RECORDED`` is a data-quality state
    meaning "no chain rows were imported for this candidate", and it exists because the view LEFT
    JOINs ``candidate_chains`` — a candidate with no chains still produces a group, and ``bool_or``
    over zero rows returns NULL rather than false, so without an explicit guard such a row falls
    through to the ``ELSE`` and is mislabelled as a lone chain.

    No candidate in the current corpus reaches it (all 14 have chains), so it is reachable but
    unreached. It is a member anyway: the row that would hit it is a row whose chain import failed,
    which is precisely when the API should be able to say "unknown" rather than raise converting a
    string it has no member for.

    Note the deliberate asymmetry with the catalog: there are only **three** INTERFACE metric rows
    per interface-dependent column, not four. A ``NO_CHAINS_RECORDED`` candidate's ``boltz2.iptm``
    therefore resolves to no metric at all — which is why ``ScoreEntry.metric`` is nullable.
    """

    NO_CHAINS_RECORDED = "no chains recorded"
    ANTIBODY_TARGET_COMPLEX = "antibody-target complex"
    ANTIBODY_ONLY_HL_PAIRING = "antibody only (H/L pairing)"
    SINGLE_CHAIN_NO_INTERFACE = "single chain (no interface)"
