from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app

FIXTURES = Path(__file__).parent / "fixtures"
DATA_ROOT = Path(__file__).parent.parent / "data"


client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_snapshot_endpoint():
    response = client.get(
        "/snapshot",
        params={
            "csv_path": str(FIXTURES / "bookings_sample.csv"),
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
            "inventory_listings": 30,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["portfolio_snapshot"]["bookings_count"] == 4
    assert "breakdown_snapshots" in payload


def test_properties_endpoint():
    response = client.get("/properties", params={"data_root": str(DATA_ROOT)})
    assert response.status_code == 200
    assert any(p["id"] == "LAFAVE" for p in response.json()["properties"])
