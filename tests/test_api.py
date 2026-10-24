import time

from fastapi.testclient import TestClient

from prguard.api import app


def test_api_validates_review_input():
    client = TestClient(app)
    response = client.post("/reviews", json={"pr_url": "https://example.com/nope"})
    assert response.status_code == 422


def test_api_returns_404_for_unknown_run():
    client = TestClient(app)
    response = client.get("/reviews/unknown/result")
    assert response.status_code == 404


def test_dashboard_lists_demo_cases_and_benchmarks():
    client = TestClient(app)
    demos = client.get("/api/demos")
    benchmarks = client.get("/api/benchmarks")
    assert demos.status_code == 200
    assert {case["case_id"] for case in demos.json()["cases"]} == {"sql-injection-05", "null-deref-01", "docs-only-09"}
    assert benchmarks.status_code == 200
    assert "benchmarks" in benchmarks.json()


def test_dashboard_demo_review_publishes_events_and_findings():
    client = TestClient(app)
    created = client.post("/api/reviews", json={
        "mode": "demo",
        "demo_case_id": "sql-injection-05",
        "strategy": "dynamic",
    })
    assert created.status_code == 202
    run_id = created.json()["run_id"]

    result = None
    for _ in range(30):
        result = client.get(f"/api/reviews/{run_id}").json()
        if result["status"] in {"completed", "partial", "failed"}:
            break
        time.sleep(0.05)
    assert result is not None
    assert result["status"] in {"completed", "partial"}
    assert result["mode"] == "demo"
    assert result["findings"]
    finding = result["findings"][0]
    assert any("\u4e00" <= char <= "\u9fff" for char in finding["description"])
    assert any("\u4e00" <= char <= "\u9fff" for char in finding["suggestion"])
    assert any(event["event_type"] == "agent" for event in result["events"])
    assert client.get(f"/api/reviews/{run_id}/findings").json()["findings"]
    report = client.get(f"/api/reviews/{run_id}/report/md")
    assert report.status_code == 200
    assert "修复建议：" in report.text
