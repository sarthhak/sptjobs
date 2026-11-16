from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.audit.container import Container
from app.audit.describe import enrich_findings, inventory, trigger_conditions
from app.audit.rules import run_audit
from app.config import Settings
from app.context import AppContext
from app.ga4_audit import run_ga4_audit
from app.main import create_app
from app.store import Store
from tests.conftest import FakeGA4


class MessyGA4(FakeGA4):
    """A property with most of the problems the GA4 audit looks for."""

    def property_config(self, prop):
        return {
            "errors": {},
            "property": {"displayName": "Example site", "timeZone": "Asia/Kolkata", "currencyCode": ""},
            "data_retention": {"eventDataRetention": "TWO_MONTHS"},
            "data_streams": [{"name": "properties/123/dataStreams/1", "type": "WEB_DATA_STREAM", "displayName": "Web",
                              "webStreamData": {"measurementId": "G-ABC123", "defaultUri": "https://www.example.com"}}],
            "key_events": [{"eventName": "purchase"}, {"eventName": "generate_lead"}],
            "custom_dimensions": [], "custom_metrics": [], "google_ads_links": [],
            "enhanced_measurement": {"properties/123/dataStreams/1": {"streamEnabled": True, "scrollsEnabled": False}},
        }

    def run_report(self, prop, spec):
        dims, mets = spec.get("dimensions", []), spec["metrics"]
        if dims == [] and "sessions" in mets:
            rows = [{"sessions": 1000, "totalUsers": 800, "engagementRate": 0.55, "keyEvents": 40, "eventCount": 9000}]
        elif dims == [] and "purchaseRevenue" in mets:
            rows = [{"ecommercePurchases": 12, "purchaseRevenue": 0}]
        elif dims == ["sessionDefaultChannelGroup"]:
            rows = [{"sessionDefaultChannelGroup": "Organic Search", "sessions": 700}, {"sessionDefaultChannelGroup": "Unassigned", "sessions": 200},
                    {"sessionDefaultChannelGroup": "Direct", "sessions": 100}]
        elif dims == ["sessionSource", "sessionMedium"]:
            rows = [{"sessionSource": "google", "sessionMedium": "organic", "sessions": 700},
                    {"sessionSource": "shop.example.com", "sessionMedium": "referral", "sessions": 40},
                    {"sessionSource": "paypal.com", "sessionMedium": "referral", "sessions": 15},
                    {"sessionSource": "(not set)", "sessionMedium": "(not set)", "sessions": 80}]
        elif dims == ["landingPage"]:
            rows = [{"landingPage": "/", "sessions": 900}, {"landingPage": "(not set)", "sessions": 100}]
        elif dims == ["pageLocation"]:
            rows = [{"pageLocation": "https://www.example.com/thanks?email=jane%40doe.com&id=4", "screenPageViews": 3}]
        elif dims == ["eventName"]:
            rows = [{"eventName": "page_view", "eventCount": 5000}, {"eventName": "purchase", "eventCount": 12},
                    {"eventName": "sign_up", "eventCount": 30}, {"eventName": "Sign_Up", "eventCount": 4}]
        elif dims == ["transactionId"]:
            rows = [{"transactionId": "T1", "eventCount": 3}, {"transactionId": "T2", "eventCount": 1},
                    {"transactionId": "(not set)", "eventCount": 2}]
        else:
            raise AssertionError(f"unexpected report {spec}")
        warnings = ["Data is subject to thresholding (low counts hidden, usually because Google signals is on)."] if dims == ["eventName"] else []
        return {"columns": dims + mets, "rows": rows, "warnings": warnings}


def test_ga4_audit_findings():
    result = run_ga4_audit(MessyGA4(), "properties/123")
    rules = {f["rule"] for f in result["findings"]}
    assert {"RETENTION_2_MONTHS", "UNASSIGNED_TRAFFIC", "SELF_REFERRALS", "PAYMENT_REFERRALS", "SOURCE_NOT_SET",
            "LANDING_NOT_SET", "PII_IN_URLS", "EVENT_NAME_VARIANTS", "KEY_EVENTS_NOT_FIRING", "PURCHASE_NO_REVENUE",
            "DUPLICATE_TRANSACTIONS", "TRANSACTION_ID_MISSING", "THRESHOLDING", "NO_CURRENCY", "NO_CUSTOM_DIMENSIONS",
            "ENHANCED_MEASUREMENT_PARTIAL", "NO_ADS_LINK"} <= rules
    by = {f["rule"]: f for f in result["findings"]}
    assert result["findings"][0]["rule"] == "PII_IN_URLS"
    assert "jane" not in by["PII_IN_URLS"]["evidence"][0] and "***" in by["PII_IN_URLS"]["evidence"][0]
    assert by["SELF_REFERRALS"]["evidence"] == ["shop.example.com / referral: 40 sessions"]
    assert by["KEY_EVENTS_NOT_FIRING"]["evidence"] == ["generate_lead"]
    assert by["UNASSIGNED_TRAFFIC"]["severity"] == "high"  # 20%
    assert result["settings"]["Data streams"].startswith("Web (G-ABC123")
    assert result["errors"] == {}


def test_ga4_audit_survives_failing_queries():
    class Broken(MessyGA4):
        def run_report(self, prop, spec):
            if spec.get("dimensions") == ["pageLocation"]:
                raise RuntimeError("quota exceeded")
            return super().run_report(prop, spec)

    result = run_ga4_audit(Broken(), "properties/123")
    assert "pii" in result["errors"] and "PII_IN_URLS" not in {f["rule"] for f in result["findings"]}


def test_gtm_descriptions(version):
    c = Container(version)
    findings = enrich_findings(c, run_audit(c, r"^[^-]+ - [^-]+ - .+$")["findings"])
    dup = next(f for f in findings if f["rule"] == "DUPLICATE_TAG")
    assert dup["category"] == "Data quality" and "inflating" in dup["impact"]
    a = dup["affected"][0]
    assert a["type"] == "GA4 Event" and a["triggers"] == "CE - purchase"
    assert "Event name: purchase" in a["details"] and "transaction_id={{DLV - transaction_id}}" in a["details"]
    inv = inventory(c, findings)
    ce = next(t for t in inv["triggers"] if t["id"] == "10")
    assert ce["conditions"] == "Event: {{_event}} equals purchase"
    assert set(ce["used_by"]) >= {"GA4 - Event - purchase", "Ads - Conversion - purchase"}
    dlv = next(v for v in inv["variables"] if v["id"] == "1")
    assert dlv["type"] == "Data Layer Variable" and dlv["details"] == "Data layer key: ecommerce.transaction_id"
    assert any("Tag: GA4 - Event - purchase" == u for u in dlv["used_by"])
    tag2 = next(t for t in inv["tags"] if t["id"] == "2")
    assert tag2["issues"] and tag2["fires_on"] == ["CE - purchase"]
    assert next(t for t in inv["tags"] if t["id"] == "1")["fires_on"] == ["All Pages"]
    assert trigger_conditions({"type": "linkClick", "filter": [{"type": "contains", "parameter": [
        {"key": "arg0", "value": "{{Click URL}}"}, {"key": "arg1", "value": ".pdf"}, {"key": "negate", "value": "true"}]}]}) \
        == "{{Click URL}} does not contains .pdf"


@pytest.fixture
def client(tmp_path, fake_gtm):
    settings = Settings(session_secret="s", data_dir=tmp_path)
    ctx = AppContext(settings=settings, store=Store(tmp_path / "t.db"), ga4=MessyGA4(), gtm=fake_gtm, anthropic=None,
                     verify_site=None)
    return TestClient(create_app(ctx))


def test_excel_exports(client, fake_gtm):
    aid = client.post("/api/audits", json={"container_path": fake_gtm.container_path}).json()["id"]
    detail = client.get(f"/api/audits/{aid}").json()
    assert detail["inventory"]["tags"] and detail["findings"][0]["affected"] and detail["findings"][0]["ref"] == "F-01"
    assert detail["scorecard"][-1]["area"] == "Total" and detail["scorecard"][-1]["total"] == len(detail["findings"])
    assert len(detail["top_priorities"]) == 5 and detail["action_plan"] and detail["consent_matrix"]
    resp = client.get(f"/api/audits/{aid}/export.xlsx")
    assert resp.status_code == 200 and "gtm-audit-GTM-TEST1" in resp.headers["content-disposition"]
    wb = load_workbook(BytesIO(resp.content))
    assert wb.sheetnames == ["Executive Summary", "Findings Log", "Action Plan", "Consent Matrix", "Tag Inventory",
                             "Trigger Inventory", "Variable Inventory", "Method & Legend"]
    log = wb["Findings Log"]
    header_row = next(r for r in range(1, 6) if log.cell(r, 1).value == "ID")
    assert log.cell(header_row + 1, 1).value == "F-01" and log.cell(header_row + 1, 3).value == "Critical"
    assert log.max_row - header_row == len(detail["findings"])

    gid = client.post("/api/ga4-audits", json={"property": "properties/123"}).json()["id"]
    assert client.get("/api/ga4-audits").json()[0]["property_name"] == "Example site"
    assert client.post("/api/ga4-audits", json={"property": "123"}).status_code == 400
    resp = client.get(f"/api/ga4-audits/{gid}/export.xlsx")
    wb = load_workbook(BytesIO(resp.content))
    assert wb.sheetnames == ["Summary", "Findings", "Property settings", "Channels (28 days)", "Events (28 days)"]
    assert wb["Findings"]["D2"].value.startswith("Possible personal data")
