"""The two inputs to `database_url`'s skip-or-fail decision.

`_docker_is_running()` answers "can the database tests run?" and `_running_in_ci()` answers "is a
No louder than a skip?". Between them they decide whether a missing daemon quietly skips every
database test or fails the build, so both are worth their own tests: get either wrong and the
failure is a GREEN build with no database coverage, including
`test_the_same_key_means_different_things_per_candidate`, the one test guarding the ipTM finding.
Nothing else in the suite would notice, because the tests would simply not run.

Written to hold whether or not Docker is up locally.
"""

from __future__ import annotations

import os

import pytest
from testcontainers.core import docker_client as tc_docker_client
from testcontainers.core.config import testcontainers_config

from tests.conftest import _docker_is_running, _running_in_ci

# A registry that cannot resolve, with credentials that cannot work.
_BOGUS_AUTH = '{"auths":{"registry.invalid.example":{"auth":"dXNlcjpwYXNz"}}}'

# A syntactically valid daemon address with nothing listening on it.
_DEAD_HOST = "tcp://127.0.0.1:1"


def test_the_probe_ignores_docker_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reachability and registry credentials are unrelated questions.

    testcontainers' own `DockerClient.__init__` performs a registry login when `DOCKER_AUTH_CONFIG`
    is set, so probing through it made an unreachable or rate-limited registry indistinguishable
    from an absent daemon. Compared against a baseline rather than asserted True so this still means
    something on a laptop with Docker stopped.
    """
    baseline = _docker_is_running()

    # NOT monkeypatch.setenv. `testcontainers.core.config` reads DOCKER_AUTH_CONFIG through a
    # dataclass `default_factory`, evaluated once when its module-level singleton is built at
    # import — so setting the environment variable from inside a test has no effect at all, and a
    # test written that way passes against the bug. (It did. That is how this comment exists.)
    monkeypatch.setattr(testcontainers_config, "_docker_auth_config", _BOGUS_AUTH)

    assert _docker_is_running() == baseline, "a bad registry credential is not an absent daemon"


def test_the_probe_does_not_rewrite_docker_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """`DockerClient.__init__` writes `os.environ["DOCKER_HOST"]` as a side effect of connecting.

    Harmless-looking, but it is process-wide state set by a function whose job is to answer a
    yes/no question — and it changes what every later `docker.from_env()` in the run resolves to.
    """
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    # Forced, because the write is conditional on the host resolving to something. On a plain
    # Docker Desktop machine `get_docker_host()` returns None and the old code's `if docker_host:`
    # never fired — so without this the test is vacuous exactly where it runs. The address is a
    # real one with nothing behind it: the probe will answer False, which is not what is asserted.
    monkeypatch.setattr(tc_docker_client, "get_docker_host", lambda: _DEAD_HOST)

    _docker_is_running()

    assert "DOCKER_HOST" not in os.environ


class TestCiDetection:
    """`_running_in_ci` was `os.environ.get("CI")` — a presence test, so `CI=false` meant yes.

    `monkeypatch.setenv` is the right tool here and a smell elsewhere (see CLAUDE.md): this reads
    `os.environ` live on every call, so the environment IS where the value is read. Contrast
    tests/conftest.py's Docker probe, where the same idiom asserted nothing because the library had
    already cached the variable at import.
    """

    @pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "maybe"])
    def test_anything_not_explicitly_negative_counts_as_ci(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Including "maybe", deliberately.

        An unrecognized value must fail loudly rather than skip silently: mistaking real CI for
        local means every database test is skipped and the build ships green with no coverage,
        while mistaking local for CI is a one-line diagnosis.
        """
        monkeypatch.setenv("CI", value)

        assert _running_in_ci() is True

    @pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off", "", "  "])
    def test_explicitly_negative_values_do_not(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """The bug: all of these used to mean "yes, this is CI" and turned a skip into a failure."""
        monkeypatch.setenv("CI", value)

        assert _running_in_ci() is False

    def test_unset_is_not_ci(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CI", raising=False)

        assert _running_in_ci() is False
