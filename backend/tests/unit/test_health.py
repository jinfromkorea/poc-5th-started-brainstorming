from __future__ import annotations


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_prereqs_reports_all_five_checks(client):
    resp = client.get("/prereqs")
    assert resp.status_code == 200
    body = resp.json()
    names = {c["name"] for c in body["checks"]}
    assert names == {"Java", "Maven", "Python", "Trivy", "npm"}
    assert isinstance(body["ok"], bool)
