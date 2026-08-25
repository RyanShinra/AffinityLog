"""`MetricKey` must keep mirroring `ScoreKey`, and only a test can enforce that.

WHY. `scripts/extract_score_keys.py` derives catalog identities from the corpus; `app/catalog/keys.py`
derives them from a key string at query time. The same four fields, assembled two ways — and the
script's docstring has always said the two must not drift. Nothing checked it, and they did: from
stage 3 until stage 5 of `docs/type-safety-plan.md`, `ScoreKey` carried `ModuleName`/`ColumnKey`/
`VariantName` while `MetricKey` still had bare `str` on all three.

MYPY CANNOT CATCH THIS, WHICH IS THE WHOLE POINT. A `NewType` is a subtype, so a `str` slot accepts
a `ModuleName` and simply forgets it — legal, lossy, silent:

    def narrowing_is_silent(m: ModuleName) -> tuple[str, ...]:
        return (m,)          # Success: no issues found — the ModuleName is gone

The type checker reports a WIDE value in a NARROW slot. This is the other direction, and stage 5
existed precisely because the compiler is blind to it. Assuming otherwise is what let the drift
happen — see `docs/pr-15-diary.md`, Act X.

An earlier version of this docstring demonstrated the point with `MetricKey(m, "col", ...)`, which
was true of the bare-`str` MetricKey it described and became FALSE the moment stage 5 fixed it:
`NamedTuple.__init__` IS typed, so that line is now an error. The silent case needs a slot that is
genuinely `str` — which is why the example above uses one, and why SQLAlchemy's declarative
`__init__` (`**kw: Any`) is the place this really bites.

`get_type_hints` rather than `__annotations__`: both modules use `from __future__ import
annotations`, so the raw annotations are STRINGS and would compare equal on spelling alone —
`"ModuleName"` from two different imports, or two different aliases that happen to share a name,
would pass. Resolving them compares the objects, and `NewType` instances are singletons.
"""

from __future__ import annotations

from typing import Final, get_args, get_type_hints

import pytest

from app.catalog.keys import MetricIdentity, ScoreKey, decompose
from scripts.extract_score_keys import MetricKey

# The catalog's natural key. `chain`/`chains` and `raw_keys` are deliberately outside it: one metric
# measured on the heavy and light chains has ONE identity, which is the collapse from 200 raw keys
# to 138 catalog rows that this script exists to show.
#
# DERIVED, NOT LISTED. The first draft wrote these four names out, and `_fields[:4] == _MIRRORED` is
# blind to anything past index 4 — so an identity that grew a fifth field would leave the new field
# unchecked while all three assertions still passed. Verified with a simulated fifth field: every
# check green, the fifth drifted. `MetricIdentity` is what actually defines the arity, so it decides
# here too.
_IDENTITY_ARITY: Final[int] = len(get_args(MetricIdentity))
_MIRRORED: Final[tuple[str, ...]] = ScoreKey._fields[:_IDENTITY_ARITY]


@pytest.mark.parametrize("field", _MIRRORED)
def test_the_identity_fields_carry_the_same_type(field: str) -> None:
    score = get_type_hints(ScoreKey)
    metric = get_type_hints(MetricKey)

    assert metric[field] == score[field], (
        f"MetricKey.{field} is {metric[field]}, but ScoreKey.{field} is {score[field]}.\n"
        "The two build the same identity from different sources and must not drift. mypy will not "
        "tell you: a NewType is a subtype, so the wider of the two accepts the narrower and forgets."
    )


def test_the_mirrored_fields_really_are_the_identity() -> None:
    """Guards the guard. If this is wrong, the test above compares the wrong things and says nothing.

    The arity comes from `MetricIdentity`; `ScoreKey.identity` must actually produce that many, and
    `MetricKey` must name the same fields in the same order over the same span. An identity that
    grows a field therefore widens `_MIRRORED` on its own, rather than leaving the new one silently
    outside the comparison.
    """
    assert len(decompose("boltz2.protein_iptm").identity) == _IDENTITY_ARITY
    assert MetricKey._fields[:_IDENTITY_ARITY] == _MIRRORED


def test_what_deliberately_does_not_mirror() -> None:
    """`chains` is plural and informational, and `raw_keys` has no counterpart at all."""
    assert ScoreKey._fields[_IDENTITY_ARITY:] == ("chain",)
    assert MetricKey._fields[_IDENTITY_ARITY:] == ("chains", "raw_keys")
