from fastapi.testclient import TestClient

from fiscal_astra.api.app import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_declares_simulation_not_executed() -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "not_configured",
        "simulation": "not_executed",
    }
