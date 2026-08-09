"""Does the application actually assemble? — no database required.

WHY THIS FILE EXISTS
--------------------
``app/main.py`` was at 0% coverage: nothing in the suite imported it, so nothing checked that
the app even constructs. That matters more than it sounds, because `main.py` is where the
pieces meet — the GraphQL router mount, the context getter, the static files directory, the
routers. A typo in any of those is an ImportError or a startup crash that every other test in
this suite would sail straight past, since they all exercise modules in isolation.

These are smoke tests, deliberately shallow. They answer "is it plugged in", not "does it work"
— resolver behaviour is exercised elsewhere.

WHY NO DATABASE
---------------
Creating an engine does not connect, and neither does entering ``AsyncSessionLocal()``. asyncpg
connects lazily on first statement execution, so every route below that does not run a query
responds normally with the container stopped. Verified that way.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_the_app_constructs() -> None:
    # If any import in the graph is broken this module fails to load and every test here errors.
    assert app.title == "AffinityLog"


def test_health_probe_responds() -> None:
    # The Docker/ECS health probe. Deliberately self-contained — no domain schema, no database —
    # which is exactly why it survived the schema-first redesign that cleared the app layer.
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_expected_routes_are_mounted() -> None:
    """The whole documented HTTP surface, in one assertion.

    Read from the OpenAPI schema rather than `app.routes`, because included routers appear there
    as opaque `_IncludedRouter` objects whose own paths are None — the child routes are not
    flattened. OpenAPI reports what a client can actually reach.

    `/demo` is the reason this test earns its place: it queries the `candidate_summary` view, so
    it cannot be exercised without Postgres, but its *mounting* can be checked here for free.
    """
    assert sorted(app.openapi()["paths"]) == ["/demo", "/demo/pdb/{candidate_id}", "/graphql", "/health"]


def test_graphql_is_mounted_and_the_playground_renders() -> None:
    """A GET on /graphql serves the in-browser IDE.

    Worth its own test because the context getter runs for this request too, not just for
    queries — FastAPI resolves it before Strawberry sees the request, so a context getter that
    raises takes the playground down along with the API.
    """
    response = TestClient(app).get("/graphql", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert "graphiql" in response.text.lower()


def test_graphql_accepts_a_query_that_needs_no_database() -> None:
    # Introspection exercises the whole POST path — context getter, schema, executor — without
    # any resolver running, so it stays database-free while still proving the mount is real.
    response = TestClient(app).post("/graphql", json={"query": "{ __schema { queryType { name } } }"})
    assert response.status_code == 200
    body = response.json()
    assert "errors" not in body, body
    assert body["data"]["__schema"]["queryType"]["name"] == "Query"


def test_static_files_are_served() -> None:
    # The demo vendors 3Dmol locally so it needs no network; if this mount breaks, the 3D viewer
    # silently degrades to a blank panel rather than erroring.
    response = TestClient(app).get("/static/vendor/3Dmol-LICENSE.txt")
    assert response.status_code == 200
