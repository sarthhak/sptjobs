import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from anthropic.types.beta import BetaMessage
from fastapi.testclient import TestClient

from app.config import Settings
from app.context import AppContext
from app.main import create_app
from app.store import Store
from tests.conftest import FakeGA4


async def fake_verify(url, wait_ms=4000, click_selector=None):
    return {"url": url, "ga4_hits": [{"event": "page_view"}], "gtm_containers_requested": ["GTM-TEST1"]}


def message(content, stop_reason):
    return BetaMessage.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": content,
        "stop_reason": stop_reason, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5},
    })


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for block in self.msg.content:
                if block.type == "text":
                    yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=block.text))
                elif block.type == "tool_use":
                    yield SimpleNamespace(type="content_block_start", content_block=block)
        return gen()

    async def get_final_message(self):
        return self.msg


class FakeAnthropic:
    """Replays scripted assistant messages and records each request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        return FakeStream(self.script.pop(0))


@pytest.fixture
def make_client(tmp_path, fake_gtm):
    def make(password=None, writes=False, anthropic=None):
        settings = Settings(app_password=password, session_secret="test-secret", data_dir=tmp_path, allow_gtm_writes=writes,
                            site_url="https://example.com" if writes else None)
        ctx = AppContext(settings=settings, store=Store(tmp_path / "t.db"), ga4=FakeGA4(), gtm=fake_gtm, anthropic=anthropic,
                         verify_site=fake_verify, google_status=lambda: "connected via test")
        return TestClient(create_app(ctx)), ctx
    return make


def sse_events(resp):
    return [json.loads(line[6:]) for line in resp.text.split("\n\n") if line.startswith("data: ")]


def test_password_protection(make_client):
    client, _ = make_client(password="s3cret")
    assert client.get("/api/status").status_code == 401
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    bad = client.post("/login", content="password=nope", headers={"Content-Type": "application/x-www-form-urlencoded"}, follow_redirects=False)
    assert "error" in bad.headers["location"]
    ok = client.post("/login", content="password=s3cret", headers={"Content-Type": "application/x-www-form-urlencoded"}, follow_redirects=False)
    assert ok.status_code == 303
    assert client.get("/api/status").json()["password_protected"] is True
    assert client.get("/static/app.js").status_code == 200


def test_audit_to_publish_flow(make_client, fake_gtm):
    client, ctx = make_client(writes=True)
    audit = client.post("/api/audits", json={"container_path": fake_gtm.container_path}).json()
    assert "version_snapshot" not in audit and audit["score"] < 100
    dup = next(f["id"] for f in audit["findings"] if f["rule"] == "DUPLICATE_TAG")
    cs = client.post(f"/api/audits/{audit['id']}/changeset", json={"finding_ids": [dup]}).json()
    detail = client.get(f"/api/changesets/{cs['id']}").json()
    assert detail["status"] == "proposed" and detail["ops"][0]["op"] == "delete"

    assert client.post(f"/api/changesets/{cs['id']}/publish", json={"confirm": "PUBLISH"}).status_code == 409  # not applied yet
    applied = client.post(f"/api/changesets/{cs['id']}/apply").json()
    assert applied["status"] == "applied" and applied["workspace_path"]
    assert client.post(f"/api/changesets/{cs['id']}/publish", json={}).status_code == 400  # needs typed confirmation
    published = client.post(f"/api/changesets/{cs['id']}/publish", json={"confirm": "PUBLISH"}).json()
    assert published["status"] == "published" and fake_gtm.live_id == "43"
    assert published["verification"]["ga4_hits"]
    rolled = client.post(f"/api/changesets/{cs['id']}/rollback", json={"confirm": "ROLLBACK"}).json()
    assert rolled["status"] == "rolled_back" and fake_gtm.live_id == "42"


def test_writes_disabled_blocks_apply(make_client, fake_gtm):
    client, _ = make_client(writes=False)
    audit = client.post("/api/audits", json={"container_path": fake_gtm.container_path}).json()
    fixable = [f["id"] for f in audit["findings"] if f["fixable"]][:1]
    cs = client.post(f"/api/audits/{audit['id']}/changeset", json={"finding_ids": fixable}).json()
    r = client.post(f"/api/changesets/{cs['id']}/apply")
    assert r.status_code == 403 and "ALLOW_GTM_WRITES" in r.json()["detail"]
    assert not any(call[0] == "create_workspace" for call in fake_gtm.calls)


def test_chat_runs_tools_and_proposes_changes(make_client, fake_gtm):
    script = [
        message([{"type": "thinking", "thinking": "Audit first.", "signature": "sig"},
                 {"type": "tool_use", "id": "tu_1", "name": "gtm_run_audit", "input": {"container_path": fake_gtm.container_path}}], "tool_use"),
        message([{"type": "tool_use", "id": "tu_2", "name": "gtm_propose_changes",
                  "input": {"container_path": fake_gtm.container_path, "audit_id": 1, "summary": "Pause UA",
                            "custom_ops": [{"op": "update", "kind": "tag", "entity_id": "6", "patch": {"paused": True}, "reason": "UA is dead"}]}},
                 {"type": "tool_use", "id": "tu_3", "name": "ga4_run_report", "input": {"property": "bad"}}], "tool_use"),
        message([{"type": "text", "text": "I proposed change set #1."}], "end_turn"),
    ]
    fake = FakeAnthropic(script)
    client, ctx = make_client(anthropic=fake)
    cid = client.post("/api/conversations").json()["id"]
    events = sse_events(client.post(f"/api/conversations/{cid}/chat", json={"message": "Audit my container"}))
    types = [e["type"] for e in events]
    assert types[-1] == "done"
    assert "audit" in types and "changeset" in types
    assert any(e["type"] == "text" and "change set #1" in e["text"] for e in events)
    bad = next(e for e in events if e["type"] == "tool_end" and e["name"] == "ga4_run_report")
    assert bad["ok"] is False and "Invalid input" in bad["preview"]

    # The third request carries the full, append-only history: thinking block preserved, both tool results in one message.
    last = fake.requests[-1]
    assert last["messages"][1]["content"][0] == {"type": "thinking", "thinking": "Audit first.", "signature": "sig"}
    results = last["messages"][4]["content"]
    assert [r["tool_use_id"] for r in results] == ["tu_2", "tu_3"] and results[1]["is_error"] is True
    assert last["fallbacks"] == "default" and last["thinking"]["type"] == "adaptive"
    assert all(t["eager_input_streaming"] for t in last["tools"])
    assert ctx.store.get_changeset(1)["ops"][0]["patch"] == {"paused": True}

    convo = client.get(f"/api/conversations/{cid}").json()
    assert {i.get("type") for i in convo["items"] if i["role"] == "event"} >= {"audit", "changeset"}


def test_interrupted_tool_call_is_closed_on_next_turn(make_client):
    fake = FakeAnthropic([message([{"type": "text", "text": "Hi"}], "end_turn")])
    client, ctx = make_client(anthropic=fake)
    cid = client.post("/api/conversations").json()["id"]
    ctx.store.append_message(cid, "user", "first")
    ctx.store.append_message(cid, "assistant", [{"type": "tool_use", "id": "tu_x", "name": "ga4_list_properties", "input": {}}])
    sse_events(client.post(f"/api/conversations/{cid}/chat", json={"message": "again"}))
    msgs = fake.requests[0]["messages"]
    assert msgs[2]["content"][0]["tool_use_id"] == "tu_x" and msgs[3]["content"] == "again"


def test_chart_report_and_dashboard_tools(make_client):
    script = [
        message([{"type": "tool_use", "id": "a", "name": "ga4_run_report",
                  "input": {"property": "properties/123", "dimensions": ["date"], "metrics": ["sessions"]}}], "tool_use"),
        message([{"type": "tool_use", "id": "b", "name": "render_chart",
                  "input": {"result_id": 1, "chart_type": "line", "title": "Sessions", "x": "date", "y": ["sessions"]}},
                 {"type": "tool_use", "id": "c", "name": "save_dashboard",
                  "input": {"name": "Traffic", "property": "properties/123",
                            "widgets": [{"title": "By channel", "chart_type": "bar", "x": "sessionDefaultChannelGroup", "y": ["sessions"],
                                         "report": {"dimensions": ["sessionDefaultChannelGroup"], "metrics": ["sessions"]}}]}},
                 {"type": "tool_use", "id": "d", "name": "save_report", "input": {"title": "Weekly", "markdown": "Up.\n\n[[chart:1]]"}}], "tool_use"),
        message([{"type": "text", "text": "Done."}], "end_turn"),
    ]
    client, ctx = make_client(anthropic=FakeAnthropic(script))
    cid = client.post("/api/conversations").json()["id"]
    events = sse_events(client.post(f"/api/conversations/{cid}/chat", json={"message": "chart it"}))
    chart = next(e for e in events if e["type"] == "chart")
    assert chart["spec"]["labels"] == ["2026-11-01", "2026-11-02"]  # time axis sorted and formatted
    dash = client.get("/api/dashboards/1/data").json()
    assert dash["data"][0]["spec"]["labels"] == ["Organic Search", "Direct"]
    assert client.get("/api/reports/1").json()["markdown"].endswith("[[chart:1]]")
    assert client.get("/reports/1").status_code == 200


def test_no_claude_key_gives_clear_error(make_client):
    client, _ = make_client(anthropic=None)
    cid = client.post("/api/conversations").json()["id"]
    events = sse_events(client.post(f"/api/conversations/{cid}/chat", json={"message": "hi"}))
    assert events == [{"type": "error", "message": "ANTHROPIC_API_KEY is not set on the server."}]
