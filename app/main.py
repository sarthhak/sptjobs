"""FastAPI web app: chat with the analytics assistant, run GTM audits, review/apply/publish change sets, dashboards."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import secrets
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .agent.loop import run_turn
from .agent.tools import run_gtm_audit, strip_snapshot
from .audit.apply import ApplyError, apply_changeset, publish_workspace, rollback
from .audit.container import Container
from .audit.describe import enrich_findings, inventory
from .export import ga4_audit_xlsx, gtm_audit_xlsx
from .ga4_audit import run_ga4_audit
from .audit.fixes import build_changeset
from .charts import build_chart
from .config import load_settings
from .context import AppContext
from .google_oauth import OAuthError, authorization_url, exchange_code, save_token

log = logging.getLogger("analytics_expert")
STATIC = Path(__file__).parent / "static"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SESSION_COOKIE = "ae_session"
SESSION_MAX_AGE = 7 * 24 * 3600
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def build_default_context() -> AppContext:
    import anthropic

    from .ga4 import GA4Client
    from .google_auth import GoogleAuthMissing, GoogleCredentials
    from .gtm import GTMClient
    from .store import Store
    from .verify import verify_site

    settings = load_settings()
    creds = GoogleCredentials(settings.google_service_account_file, settings.google_token_file,
                               settings.google_service_account_json)

    def google_status() -> str:
        try:
            creds.get()
            return f"connected via {creds.describe()}"
        except GoogleAuthMissing as e:
            return f"not connected: {e}"
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"

    try:
        client = anthropic.AsyncAnthropic()
        if not (client.api_key or client.auth_token):
            client = None  # no credentials found; the UI shows a clear message
    except Exception:  # noqa: BLE001
        client = None
    return AppContext(settings=settings, store=Store(settings.db_path), ga4=GA4Client(creds),
                      gtm=GTMClient(creds, settings.gtm_write_interval), anthropic=client,
                      verify_site=verify_site, google_status=google_status, google_reset=creds.reset)


def _diff_ops(ops: list[dict]) -> list[dict]:
    out = []
    for o in ops:
        item = dict(o)
        if o["op"] == "update":
            item["after"] = {**o["before"], **o["patch"]}
        elif o["op"] == "create":
            item["after"] = o["body"]
        else:
            item["after"] = None
        out.append(item)
    return out


def create_app(ctx: AppContext | None = None) -> FastAPI:
    ctx = ctx or build_default_context()
    settings = ctx.settings
    signer = URLSafeTimedSerializer(settings.session_secret, salt="session")
    app = FastAPI(title="Analytics Expert", docs_url=None, redoc_url=None)
    app.state.ctx = ctx
    conversation_locks: dict[int, asyncio.Lock] = {}

    # -- access control ----------------------------------------------------------
    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        # Google's sign-in redirect is cross-site, so the strict session cookie isn't sent with it;
        # that request is authenticated by its signed, short-lived state value instead.
        if path in ("/login", "/healthz", "/oauth/google/callback") or path.startswith("/static/"):
            return await call_next(request)
        if settings.app_password:
            token = request.cookies.get(SESSION_COOKIE, "")
            try:
                signer.loads(token, max_age=SESSION_MAX_AGE)
            except BadSignature:
                if path.startswith("/api/"):
                    return JSONResponse({"detail": "Not logged in"}, status_code=401)
                return RedirectResponse("/login", status_code=303)
        elif (request.client.host if request.client else "") not in LOCAL_HOSTS:
            return JSONResponse({"detail": "Set APP_PASSWORD to allow access from other machines."}, status_code=403)
        return await call_next(request)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/login", response_class=HTMLResponse)
    async def login_page():
        return (STATIC / "login.html").read_text()

    @app.post("/login")
    async def login(request: Request):
        form = parse_qs((await request.body()).decode())
        password = (form.get("password") or [""])[0]
        if not settings.app_password or not hmac.compare_digest(password.encode(), settings.app_password.encode()):
            await asyncio.sleep(1.5)
            return RedirectResponse("/login?error=1", status_code=303)
        resp = RedirectResponse("/", status_code=303)
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        resp.set_cookie(SESSION_COOKIE, signer.dumps("owner"), max_age=SESSION_MAX_AGE, httponly=True, samesite="strict", secure=secure)
        return resp

    @app.post("/logout")
    async def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    async def status(request: Request):
        google = await asyncio.to_thread(ctx.google_status)
        return {"model": settings.model, "writes_enabled": settings.allow_gtm_writes, "google": google,
                "claude_configured": ctx.anthropic is not None, "site_url": settings.site_url,
                "password_protected": bool(settings.app_password),
                "google_signin_available": bool(settings.google_oauth_client_id and settings.google_oauth_client_secret),
                "google_signed_in": settings.google_token_file.exists(),
                "google_uses_service_account": bool(settings.google_service_account_json or settings.google_service_account_file),
                "google_redirect_uri": _redirect_uri(request)}

    # -- Connect Google account ------------------------------------------------------
    oauth_signer = URLSafeTimedSerializer(settings.session_secret, salt="google-oauth")

    def _redirect_uri(request: Request) -> str:
        base = settings.public_url or str(request.base_url).rstrip("/")
        return f"{base}/oauth/google/callback"

    @app.get("/oauth/google/start")
    async def google_start(request: Request):
        if not (settings.google_oauth_client_id and settings.google_oauth_client_secret):
            raise HTTPException(400, "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET first.")
        state = oauth_signer.dumps(secrets.token_urlsafe(16))
        return RedirectResponse(authorization_url(settings.google_oauth_client_id, _redirect_uri(request), state), status_code=303)

    @app.get("/oauth/google/callback")
    async def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
        try:
            oauth_signer.loads(state, max_age=600)
        except BadSignature:
            return RedirectResponse("/#settings/expired", status_code=303)
        if error or not code:
            return RedirectResponse("/#settings/cancelled", status_code=303)
        try:
            token = await asyncio.to_thread(exchange_code, settings.google_oauth_client_id,
                                            settings.google_oauth_client_secret, code, _redirect_uri(request))
            save_token(settings.google_token_file, token, settings.google_oauth_client_id, settings.google_oauth_client_secret)
        except OAuthError as e:
            log.warning("Google sign-in failed: %s", e)
            return RedirectResponse("/#settings/failed", status_code=303)
        ctx.google_reset()
        return RedirectResponse("/#settings/connected", status_code=303)

    @app.post("/api/google/disconnect")
    async def google_disconnect():
        settings.google_token_file.unlink(missing_ok=True)
        ctx.google_reset()
        return {"ok": True}

    # -- conversations -----------------------------------------------------------
    @app.get("/api/conversations")
    async def conversations():
        return ctx.store.list_conversations()

    @app.post("/api/conversations")
    async def new_conversation():
        cid = ctx.store.create_conversation("New conversation")
        return ctx.store.get_conversation(cid)

    @app.delete("/api/conversations/{cid}")
    async def delete_conversation(cid: int):
        ctx.store.delete_conversation(cid)
        return {"ok": True}

    @app.get("/api/conversations/{cid}")
    async def conversation(cid: int):
        conv = ctx.store.get_conversation(cid)
        if not conv:
            raise HTTPException(404)
        items, tool_names = [], {}
        for m in ctx.store.messages(cid):
            content = m["content"]
            if m["role"] == "user" and isinstance(content, str):
                items.append({"role": "user", "text": content})
                continue
            if m["role"] == "assistant":
                for b in content:
                    if b.get("type") == "text" and b.get("text"):
                        items.append({"role": "assistant", "text": b["text"]})
                    elif b.get("type") == "tool_use":
                        tool_names[b["id"]] = b["name"]
                        items.append({"role": "tool", "name": b["name"], "input": b.get("input")})
                continue
            for b in content:  # tool results: surface charts, change sets, reports, dashboards, audits
                if b.get("type") != "tool_result" or b.get("is_error"):
                    continue
                try:
                    data = json.loads(b["content"]) if isinstance(b["content"], str) else {}
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(data, dict):
                    continue
                if "chart_id" in data:
                    chart = ctx.store.get_chart(data["chart_id"])
                    if chart:
                        items.append({"role": "event", "type": "chart", "id": chart["id"], "spec": chart["spec"]})
                for key, typ in (("changeset_id", "changeset"), ("report_id", "report"), ("dashboard_id", "dashboard"), ("audit_id", "audit"), ("ga4_audit_id", "ga4audit")):
                    if key in data:
                        items.append({"role": "event", "type": typ, "id": data[key], "score": data.get("score"),
                                      "container": data.get("container") or data.get("property_name")})
        return {"conversation": conv, "items": items}

    @app.post("/api/conversations/{cid}/chat")
    async def chat(cid: int, request: Request):
        conv = ctx.store.get_conversation(cid)
        if not conv:
            raise HTTPException(404)
        body = await request.json()
        text = (body.get("message") or "").strip()
        if not text:
            raise HTTPException(400, "Empty message")
        lock = conversation_locks.setdefault(cid, asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "This conversation is already answering a message.")
        if conv["title"] == "New conversation":
            ctx.store.rename_conversation(cid, text[:60])

        async def events():
            async with lock:
                try:
                    async for ev in run_turn(ctx, cid, text):
                        yield f"data: {json.dumps(ev, default=str)}\n\n"
                except Exception as e:  # noqa: BLE001
                    log.exception("chat turn failed")
                    yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/charts/{chart_id}")
    async def chart(chart_id: int):
        c = ctx.store.get_chart(chart_id)
        if not c:
            raise HTTPException(404)
        return c

    # -- GA4 / GTM lookups ----------------------------------------------------------
    @app.get("/api/containers")
    async def containers():
        try:
            return await asyncio.to_thread(ctx.gtm.list_containers)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"GTM: {e}")

    @app.get("/api/properties")
    async def properties():
        try:
            return await asyncio.to_thread(ctx.ga4.list_properties)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"GA4: {e}")

    # -- audits ----------------------------------------------------------------------
    @app.get("/api/audits")
    async def audits():
        return ctx.store.list_audits()

    @app.post("/api/audits")
    async def new_audit(request: Request):
        body = await request.json()
        path = body.get("container_path", "")
        try:
            audit_id, result = await run_gtm_audit(ctx, path)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Audit failed: {e}")
        return {"id": audit_id, **strip_snapshot(result)}

    def _full_audit(aid: int) -> dict:
        a = ctx.store.get_audit(aid)
        if not a:
            raise HTTPException(404)
        c = Container(a["result"]["version_snapshot"])
        out = {**{k: v for k, v in a.items() if k != "result"}, **strip_snapshot(a["result"])}
        out["findings"] = enrich_findings(c, out["findings"])
        out["inventory"] = inventory(c, out["findings"])
        return out

    @app.get("/api/audits/{aid}")
    async def audit(aid: int):
        return _full_audit(aid)

    @app.get("/api/audits/{aid}/export.xlsx")
    async def audit_export(aid: int):
        a = _full_audit(aid)
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", f"gtm-audit-{a.get('public_id') or a['container']}-{aid}")
        return Response(gtm_audit_xlsx(a), media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})

    # -- GA4 audits -------------------------------------------------------------------
    @app.get("/api/ga4-audits")
    async def ga4_audits():
        return ctx.store.list_ga4_audits()

    @app.post("/api/ga4-audits")
    async def new_ga4_audit(request: Request):
        prop = (await request.json()).get("property", "")
        if not re.fullmatch(r"properties/\d+", prop):
            raise HTTPException(400, "property must look like properties/123456789")
        try:
            result = await asyncio.to_thread(run_ga4_audit, ctx.ga4, prop)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"GA4 audit failed: {e}")
        return {"id": ctx.store.save_ga4_audit(result)}

    def _ga4_audit(aid: int) -> dict:
        a = ctx.store.get_ga4_audit(aid)
        if not a:
            raise HTTPException(404)
        return {"id": a["id"], "created": a["created"], **a["result"]}

    @app.get("/api/ga4-audits/{aid}")
    async def ga4_audit(aid: int):
        return _ga4_audit(aid)

    @app.get("/api/ga4-audits/{aid}/export.xlsx")
    async def ga4_audit_export(aid: int):
        a = _ga4_audit(aid)
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", f"ga4-audit-{a['property_name']}-{aid}")
        return Response(ga4_audit_xlsx(a), media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})

    @app.post("/api/audits/{aid}/changeset")
    async def changeset_from_audit(aid: int, request: Request):
        a = ctx.store.get_audit(aid)
        if not a:
            raise HTTPException(404)
        body = await request.json()
        ids = body.get("finding_ids") or []
        ops, errors = build_changeset(Container(a["result"]["version_snapshot"]), a["result"]["findings"], ids, [])
        if not ops:
            raise HTTPException(400, "; ".join(errors) or "No fixable findings selected")
        csid = ctx.store.create_changeset(container_path=a["container_path"], audit_id=aid, version_id=a["version_id"],
                                          summary=body.get("summary") or f"Fixes for {len(ids)} audit findings", ops=ops, errors=errors)
        return {"id": csid, "errors": errors}

    # -- change sets -------------------------------------------------------------------
    def _cs(csid: int) -> dict:
        cs = ctx.store.get_changeset(csid)
        if not cs:
            raise HTTPException(404)
        return cs

    def _require_writes():
        if not settings.allow_gtm_writes:
            raise HTTPException(403, "GTM writes are disabled. Set ALLOW_GTM_WRITES=true on the server to enable apply and publish.")

    @app.get("/api/changesets")
    async def changesets():
        return ctx.store.list_changesets()

    @app.get("/api/changesets/{csid}")
    async def get_changeset(csid: int):
        cs = _cs(csid)
        cs["ops"] = _diff_ops(cs["ops"])
        return cs

    @app.post("/api/changesets/{csid}/apply")
    async def apply(csid: int):
        _require_writes()
        cs = _cs(csid)
        if cs["status"] != "proposed":
            raise HTTPException(409, f"Change set is {cs['status']}")
        ctx.store.update_changeset(csid, status="applying")
        try:
            res = await asyncio.to_thread(apply_changeset, ctx.gtm, cs["container_path"], csid, cs["ops"], cs["version_id"])
        except Exception as e:  # noqa: BLE001
            ctx.store.update_changeset(csid, status="failed", log=cs["log"] + [str(e)])
            raise HTTPException(409 if isinstance(e, ApplyError) else 502, str(e))
        ctx.store.update_changeset(csid, status="applied", workspace_path=res["workspace_path"], workspace_url=res.get("workspace_url"),
                                   previous_version_path=res["previous_live_version_path"], log=cs["log"] + res["log"], ops=cs["ops"])
        return _cs(csid)

    @app.post("/api/changesets/{csid}/publish")
    async def publish(csid: int, request: Request):
        _require_writes()
        cs = _cs(csid)
        body = await request.json()
        if body.get("confirm") != "PUBLISH":
            raise HTTPException(400, "Type PUBLISH to confirm.")
        if cs["status"] != "applied":
            raise HTTPException(409, f"Change set is {cs['status']}; apply it first")
        notes = f"{cs['summary']}\n\n" + "\n".join(f"- {o['op']} {o['kind']} '{o['name']}': {o['reason']}" for o in cs["ops"])
        try:
            res = await asyncio.to_thread(publish_workspace, ctx.gtm, cs["container_path"], cs["workspace_path"],
                                          f"AI change set #{csid}", notes[:9000])
        except Exception as e:  # noqa: BLE001
            ctx.store.update_changeset(csid, log=cs["log"] + [f"Publish failed: {e}"])
            raise HTTPException(409 if isinstance(e, ApplyError) else 502, str(e))
        entries = cs["log"] + [f"Published version {res['version_id']}"]
        verification = None
        if settings.site_url:
            try:
                verification = await ctx.verify_site(settings.site_url, 4000)
                entries.append(f"Post-publish check of {settings.site_url}: {len(verification.get('ga4_hits', []))} GA4 hits, "
                               f"containers {verification.get('gtm_containers_requested')}")
            except Exception as e:  # noqa: BLE001
                entries.append(f"Post-publish check failed: {e}")
        ctx.store.update_changeset(csid, status="published", version_path=res["version_path"], log=entries)
        return {**_cs(csid), "verification": verification}

    @app.post("/api/changesets/{csid}/discard")
    async def discard(csid: int):
        cs = _cs(csid)
        if cs["status"] not in ("proposed", "applied", "failed"):
            raise HTTPException(409, f"Change set is {cs['status']}")
        entries = list(cs["log"])
        if cs["status"] == "applied" and cs["workspace_path"]:
            _require_writes()
            try:
                await asyncio.to_thread(ctx.gtm.delete_workspace, cs["workspace_path"])
                entries.append("Deleted the GTM workspace.")
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"Could not delete workspace: {e}")
        ctx.store.update_changeset(csid, status="discarded", log=entries)
        return _cs(csid)

    @app.post("/api/changesets/{csid}/rollback")
    async def do_rollback(csid: int, request: Request):
        _require_writes()
        cs = _cs(csid)
        body = await request.json()
        if body.get("confirm") != "ROLLBACK":
            raise HTTPException(400, "Type ROLLBACK to confirm.")
        if cs["status"] != "published" or not cs["previous_version_path"]:
            raise HTTPException(409, "Only a published change set with a recorded previous version can be rolled back.")
        try:
            await asyncio.to_thread(rollback, ctx.gtm, cs["previous_version_path"])
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, str(e))
        ctx.store.update_changeset(csid, status="rolled_back", log=cs["log"] + [f"Re-published {cs['previous_version_path']}"])
        return _cs(csid)

    # -- dashboards & reports -----------------------------------------------------------
    @app.get("/api/dashboards")
    async def dashboards():
        return ctx.store.list_dashboards()

    @app.delete("/api/dashboards/{did}")
    async def delete_dashboard(did: int):
        ctx.store.delete_dashboard(did)
        return {"ok": True}

    @app.get("/api/dashboards/{did}/data")
    async def dashboard_data(did: int):
        d = ctx.store.get_dashboard(did)
        if not d:
            raise HTTPException(404)

        async def one(w):
            try:
                data = await asyncio.to_thread(ctx.ga4.run_report, d["property"], w["report"])
                return {"title": w["title"], "spec": build_chart(data, w["chart_type"], w["title"], w["x"], w["y"]), "warnings": data["warnings"]}
            except Exception as e:  # noqa: BLE001
                return {"title": w["title"], "error": str(e)[:500]}

        widgets = await asyncio.gather(*(one(w) for w in d["widgets"]))
        return {**d, "data": widgets}

    @app.get("/api/reports")
    async def reports():
        return ctx.store.list_reports()

    @app.get("/api/reports/{rid}")
    async def report_data(rid: int):
        r = ctx.store.get_report(rid)
        if not r:
            raise HTTPException(404)
        return r

    @app.get("/reports/{rid}")
    async def report_page(rid: int):
        if not ctx.store.get_report(rid):
            raise HTTPException(404)
        return FileResponse(STATIC / "report.html")

    return app


def app_factory() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    return create_app()
