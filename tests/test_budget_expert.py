import json
import time
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.config import Settings
from app.context import AppContext
from app.ga4_audit import friendly_error, run_ga4_audit
from app.main import create_app
from app.store import Store
from app.usage import cost_usd
from tests.test_app import FakeAnthropic, fake_verify, message, sse_events
from tests.test_audit_reports import MessyGA4

EXPERT = {
    "executive_summary": "Consent leaks and GA4 hygiene issues; fix consent first.",
    "top_priorities": [{"title": f"Priority {i}", "detail": "Do it.", "finding_refs": ["F-01"]} for i in range(1, 6)],
    "additional_findings": [{"area": "Measurement coverage", "severity": "critical", "effort": "M", "title": "No lead conversion is tracked",
                             "detail": "Only clicks.", "evidence": "Tag 42 paused", "impact": "No CPL.", "fix": "Add generate_lead.",
                             "validate": "Submit a form.", "owner": "Web developers"}],
    "event_model": [{"current_events": "build_and_quote_click", "proposed_event": "cta_click", "parameters": "cta_type",
                     "key_event": "No", "notes": "Consolidate."}],
    "custom_dimensions": ["cta_type"],
    "limitations": "Static review.",
}


def make(tmp_path, fake_gtm, anthropic=None):
    ctx = AppContext(settings=Settings(session_secret="s", data_dir=tmp_path), store=Store(tmp_path / "t.db"), ga4=MessyGA4(),
                     gtm=fake_gtm, anthropic=anthropic, verify_site=fake_verify)
    return TestClient(create_app(ctx)), ctx


def test_cost_estimate():
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_read_input_tokens": 2_000_000, "cache_creation_input_tokens": 0}
    assert cost_usd("claude-opus-5-5", usage) == pytest.approx(4 + 2 + 0.4)
    assert cost_usd("unknown-model", usage) == pytest.approx(6.4)  # priced as the default model


def test_spend_tracking_and_monthly_limit(tmp_path, fake_gtm):
    fake = FakeAnthropic([message([{"type": "text", "text": "Hi"}], "end_turn")] * 3)
    client, ctx = make(tmp_path, fake_gtm, fake)
    cid = client.post("/api/conversations").json()["id"]
    sse_events(client.post(f"/api/conversations/{cid}/chat", json={"message": "hello"}))
    u = client.get("/api/usage").json()
    assert u["month_usd"] > 0 and u["limit_usd"] is None and u["by_source_this_month"][0]["source"] == "chat"
    assert client.get("/api/status").json()["usage"]["month_usd"] == u["month_usd"]

    assert client.post("/api/usage/limit", json={"limit_usd": -1}).status_code == 400
    assert client.post("/api/usage/limit", json={"limit_usd": 0.000001}).json()["exceeded"] is True
    events = sse_events(client.post(f"/api/conversations/{cid}/chat", json={"message": "again"}))
    assert events[-1]["type"] == "error" and "budget" in events[-1]["message"]
    assert len(fake.requests) == 1  # no API call once the budget is used up

    assert client.post("/api/usage/limit", json={"limit_usd": None}).json()["limit_usd"] is None
    assert sse_events(client.post(f"/api/conversations/{cid}/chat", json={"message": "and again"}))[-1]["type"] == "done"


def test_expert_review(tmp_path, fake_gtm):
    fake = FakeAnthropic([message([{"type": "text", "text": json.dumps(EXPERT)}], "end_turn")])
    client, ctx = make(tmp_path, fake_gtm, fake)
    aid = client.post("/api/audits", json={"container_path": fake_gtm.container_path}).json()["id"]
    assert client.post(f"/api/audits/{aid}/expert-review").json()["status"] == "running"
    for _ in range(100):
        if client.get(f"/api/audits/{aid}/expert-review").json()["status"] != "running":
            break
        time.sleep(0.05)
    assert client.get(f"/api/audits/{aid}/expert-review").json()["status"] == "done"
    a = client.get(f"/api/audits/{aid}").json()
    req = fake.requests[0]
    assert req["output_config"]["format"]["type"] == "json_schema" and "GA4 - Event - purchase" in req["messages"][0]["content"]
    assert a["has_expert_review"] and a["executive_summary"].startswith("Consent")
    assert a["top_priorities"][0]["title"] == "Priority 1" and a["event_model"][0]["proposed_event"] == "cta_click"
    expert = [f for f in a["findings"] if f["source"] == "Expert review"]
    assert expert and expert[0]["severity"] == "critical" and expert[0]["ref"].startswith("F-")
    assert client.get("/api/usage").json()["by_source_this_month"][0]["source"] == "expert_review"
    wb = load_workbook(BytesIO(client.get(f"/api/audits/{aid}/export.xlsx").content))
    assert "Proposed Event Model" in wb.sheetnames


def test_expert_review_needs_key(tmp_path, fake_gtm):
    client, _ = make(tmp_path, fake_gtm, None)
    aid = client.post("/api/audits", json={"container_path": fake_gtm.container_path}).json()["id"]
    assert client.post(f"/api/audits/{aid}/expert-review").status_code == 400


def test_fix_single_items_of_a_finding(tmp_path, fake_gtm, version):
    version["trigger"].append({"triggerId": "30", "name": "Another unused", "type": "click"})
    fake_gtm.versions["42"] = version
    client, ctx = make(tmp_path, fake_gtm)
    aid = client.post("/api/audits", json={"container_path": fake_gtm.container_path}).json()["id"]
    unused = next(f for f in client.get(f"/api/audits/{aid}").json()["findings"] if f["finding_id"] == "UNUSED_TRIGGER")
    assert {a["id"] for a in unused["affected"]} == {"12", "30"}
    cs = client.post(f"/api/audits/{aid}/changeset", json={"finding_ids": ["UNUSED_TRIGGER@30"]}).json()
    ops = ctx.store.get_changeset(cs["id"])["ops"]
    assert [(o["op"], o["entity_id"]) for o in ops] == [("delete", "30")]


def test_ga4_errors_are_actionable_and_rollups_handled():
    raw = ('<HttpError 403 when requesting https://analyticsdata.googleapis.com/v1beta/properties/1:runReport returned '
           '"Google Analytics Data API has not been used in project 1097643816968 before or it is disabled.')
    msg = friendly_error(raw)
    assert "Google Analytics Data API is not enabled" in msg and "analyticsdata.googleapis.com?project=1097643816968" in msg

    class Disabled(MessyGA4):
        def property_config(self, prop):
            cfg = super().property_config(prop)
            cfg["property"]["propertyType"] = "PROPERTY_TYPE_ROLLUP"
            cfg["data_streams"] = []
            return cfg

        def run_report(self, prop, spec):
            raise RuntimeError(raw)

    r = run_ga4_audit(Disabled(), "properties/1")
    assert r["incomplete"] and r["score"] is None and len(r["problems"]) == 1
    assert "NO_WEB_STREAM" not in {f["rule"] for f in r["findings"]} and r["property_type"] == "Roll-up"
