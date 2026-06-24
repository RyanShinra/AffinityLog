"""
Integration tests: spin up a real Postgres (via testcontainers or TEST_DATABASE_URL),
create experiments via REST, import the sample CSV, and query via GraphQL.
"""

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_create_experiment(client: AsyncClient) -> None:
    resp = await client.post(
        "/experiments",
        json={
            "name": "HER2 Nanobody Run 1",
            "recipe_name": "BoltzGen nanobody design",
            "target_name": "HER2 extracellular domain (PDB 1S78)",
            "target_pdb_id": "1S78",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "HER2 Nanobody Run 1"
    assert uuid.UUID(data["id"])


@pytest.mark.asyncio
async def test_import_candidates(client: AsyncClient, sample_csv_bytes: bytes) -> None:
    # Create experiment
    resp = await client.post(
        "/experiments",
        json={
            "name": "Import Test",
            "recipe_name": "BoltzGen",
            "target_name": "HER2 (PDB 1S78)",
        },
    )
    exp_id = resp.json()["id"]

    # Import CSV
    resp = await client.post(
        f"/experiments/{exp_id}/candidates/import",
        files={"file": ("her2_nanobody_sample.csv", sample_csv_bytes, "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rows_imported"] == 35
    assert data["rows_skipped"] == []


@pytest.mark.asyncio
async def test_graphql_query_experiments(client: AsyncClient, sample_csv_bytes: bytes) -> None:
    # Setup
    resp = await client.post(
        "/experiments",
        json={"name": "GQL Test", "recipe_name": "BoltzGen", "target_name": "HER2"},
    )
    exp_id = resp.json()["id"]
    await client.post(
        f"/experiments/{exp_id}/candidates/import",
        files={"file": ("s.csv", sample_csv_bytes, "text/csv")},
    )

    query = """
    query {
        experiments {
            id
            name
            candidateCount
        }
    }
    """
    resp = await client.post("/graphql", json={"query": query})
    assert resp.status_code == 200
    data = resp.json()
    assert "errors" not in data
    experiments = data["data"]["experiments"]
    assert any(e["name"] == "GQL Test" for e in experiments)


@pytest.mark.asyncio
async def test_graphql_candidates_filter(client: AsyncClient, sample_csv_bytes: bytes) -> None:
    resp = await client.post(
        "/experiments",
        json={"name": "Filter Test", "recipe_name": "BoltzGen", "target_name": "HER2"},
    )
    exp_id = resp.json()["id"]
    await client.post(
        f"/experiments/{exp_id}/candidates/import",
        files={"file": ("s.csv", sample_csv_bytes, "text/csv")},
    )

    query = f"""
    query {{
        candidates(
            filter: {{
                experimentId: "{exp_id}"
                maxBindingAffinityKd: 5.0
            }}
            sortBy: BINDING_AFFINITY_KD
            direction: ASC
        ) {{
            sequenceId
            bindingAffinityKd
        }}
    }}
    """
    resp = await client.post("/graphql", json={"query": query})
    assert resp.status_code == 200
    data = resp.json()
    assert "errors" not in data
    candidates = data["data"]["candidates"]
    assert all(c["bindingAffinityKd"] <= 5.0 for c in candidates)
    # Should be sorted ascending
    kds = [c["bindingAffinityKd"] for c in candidates]
    assert kds == sorted(kds)


@pytest.mark.asyncio
async def test_graphql_top_candidates(client: AsyncClient, sample_csv_bytes: bytes) -> None:
    resp = await client.post(
        "/experiments",
        json={"name": "Top Test", "recipe_name": "BoltzGen", "target_name": "HER2"},
    )
    exp_id = resp.json()["id"]
    await client.post(
        f"/experiments/{exp_id}/candidates/import",
        files={"file": ("s.csv", sample_csv_bytes, "text/csv")},
    )

    query = f"""
    query {{
        topCandidates(experimentId: "{exp_id}", limit: 5) {{
            sequenceId
            bindingAffinityKd
            humanessScore
        }}
    }}
    """
    resp = await client.post("/graphql", json={"query": query})
    assert resp.status_code == 200
    data = resp.json()
    assert "errors" not in data
    assert len(data["data"]["topCandidates"]) == 5


@pytest.mark.asyncio
async def test_graphql_annotate_mutation(client: AsyncClient, sample_csv_bytes: bytes) -> None:
    resp = await client.post(
        "/experiments",
        json={"name": "Annotate Test", "recipe_name": "BoltzGen", "target_name": "HER2"},
    )
    exp_id = resp.json()["id"]
    await client.post(
        f"/experiments/{exp_id}/candidates/import",
        files={"file": ("s.csv", sample_csv_bytes, "text/csv")},
    )

    # Get a candidate ID
    query = f"""
    query {{
        candidates(filter: {{experimentId: "{exp_id}"}}, limit: 1) {{
            id
        }}
    }}
    """
    resp = await client.post("/graphql", json={"query": query})
    candidate_id = resp.json()["data"]["candidates"][0]["id"]

    mutation = f"""
    mutation {{
        annotateCandidate(id: "{candidate_id}", annotation: "Promising — low Kd and high humanness") {{
            id
            annotation
        }}
    }}
    """
    resp = await client.post("/graphql", json={"query": mutation})
    assert resp.status_code == 200
    data = resp.json()
    assert "errors" not in data
    assert "Promising" in data["data"]["annotateCandidate"]["annotation"]
