"""`_docker_is_running` is asked one question, and must not answer a different one.

Worth a test file of its own because of what the answer controls: `database_url` turns a False into
`pytest.fail` when `CI` is set, so a probe that is wrong about a working daemon turns a healthy
runner into a red build. Nothing else in the suite would notice — every database test would simply
stop running, which is the failure mode the CI guard exists to prevent.

Both tests are written to hold whether or not Docker is up locally.
"""

from __future__ import annotations

import os

import pytest
from testcontainers.core import docker_client as tc_docker_client
from testcontainers.core.config import testcontainers_config

from tests.conftest import _docker_is_running

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
