# tests/test_health.py
def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_readyz(client, db_session):
    resp = client.get("/readyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "db" in data["checks"]


def test_metrics_endpoint(client, db_session):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "mailfallback_accounts_total" in resp.text
    assert "mailfallback_jobs_pending" in resp.text
