import asyncio
import http.server
import threading

import pytest

from app.ga4 import build_report_body, check_property_config, flatten_report


def test_build_report_body():
    body = build_report_body({
        "start_date": "28daysAgo", "end_date": "yesterday", "dimensions": ["sessionDefaultChannelGroup"], "metrics": ["sessions", "keyEvents"],
        "filters": [{"field": "country", "op": "equals", "value": "India"}, {"field": "sessions", "op": "gt", "value": 10},
                    {"field": "pagePath", "op": "contains", "value": "/blog", "negate": True}],
        "order_by": {"field": "sessions", "desc": True}, "limit": 50,
        "compare_start_date": "56daysAgo", "compare_end_date": "29daysAgo",
    })
    assert len(body["dateRanges"]) == 2
    assert body["dimensionFilter"]["andGroup"]["expressions"][1]["notExpression"]["filter"]["fieldName"] == "pagePath"
    assert body["metricFilter"]["filter"]["numericFilter"] == {"operation": "GREATER_THAN", "value": {"int64Value": "10"}}
    assert body["orderBys"] == [{"metric": {"metricName": "sessions"}, "desc": True}]


def test_flatten_report_warnings():
    out = flatten_report({
        "dimensionHeaders": [{"name": "country"}], "metricHeaders": [{"name": "sessions", "type": "TYPE_INTEGER"}],
        "rows": [{"dimensionValues": [{"value": "India"}], "metricValues": [{"value": "12"}]}],
        "rowCount": 1, "metadata": {"subjectToThresholding": True, "currencyCode": "INR"},
    })
    assert out["rows"] == [{"country": "India", "sessions": 12}]
    assert out["warnings"] and out["currency"] == "INR"


def test_property_checks():
    checks = check_property_config({"data_retention": {"eventDataRetention": "TWO_MONTHS"}, "key_events": [], "data_streams": [],
                                    "custom_dimensions": [], "property": {"currencyCode": "USD"}})
    titles = {c["title"] for c in checks}
    assert {"Event data retention is 2 months", "No key events", "No web data stream"} <= titles


PAGE = b"""<!doctype html><html><body><button id="buy">Buy</button><script>
window.dataLayer = [{event: 'gtm.js'}];
navigator.sendBeacon('https://region1.google-analytics.com/g/collect?v=2&tid=G-TEST1&en=page_view&dl=http%3A%2F%2Flocalhost');
document.getElementById('buy').onclick = () => {
  dataLayer.push({event: 'add_to_cart'});
  fetch('https://www.google-analytics.com/g/collect?v=2&tid=G-TEST1', {method: 'POST', mode: 'no-cors',
    body: 'en=add_to_cart&ep.currency=USD\\nen=scroll&epn.percent_scrolled=90'});
};
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):
        pass


def test_verify_site_intercepts_ga4_hits():
    pytest.importorskip("playwright")
    from app.verify import verify_site

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = asyncio.run(verify_site(f"http://127.0.0.1:{server.server_port}/", 1000, "#buy"))
    finally:
        server.shutdown()
    if "error" in result:
        pytest.skip(result["error"])
    events = [(h["measurement_id"], h["event"]) for h in result["ga4_hits"]]
    assert ("G-TEST1", "page_view") in events and ("G-TEST1", "add_to_cart") in events and ("G-TEST1", "scroll") in events
    cart = next(h for h in result["ga4_hits"] if h["event"] == "add_to_cart")
    assert cart["params"] == {"currency": "USD"}
    scroll = next(h for h in result["ga4_hits"] if h["event"] == "scroll")
    assert scroll["params"] == {"percent_scrolled": "90"}
    assert {"event": "add_to_cart"} in result["data_layer"]


def test_service_account_json_from_env_variable(tmp_path):
    import json

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from app.google_auth import GoogleAuthMissing, GoogleCredentials

    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    info = {"type": "service_account", "project_id": "p", "private_key_id": "k", "private_key": pem,
            "client_email": "bot@p.iam.gserviceaccount.com", "client_id": "1", "token_uri": "https://oauth2.googleapis.com/token"}
    creds = GoogleCredentials(None, tmp_path / "none.json", json.dumps(info))
    loaded = creds._load()
    assert loaded.service_account_email == "bot@p.iam.gserviceaccount.com"
    assert "GOOGLE_SERVICE_ACCOUNT_JSON" in creds.describe()
    with pytest.raises(GoogleAuthMissing, match="not valid JSON"):
        GoogleCredentials(None, tmp_path / "none.json", "{oops")._load()
