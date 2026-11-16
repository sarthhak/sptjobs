"""Live-site check with a headless browser: which GTM containers load, which GA4 events fire, what's in the dataLayer.

GA4 hits are intercepted and answered locally, never forwarded, so checks don't add bot traffic to your reports.
"""

from __future__ import annotations

import json
import os
import re
from urllib.parse import parse_qsl, urlparse

GTM_LOAD = re.compile(r"/gtm\.js\?(?:.*&)?id=(GTM-[A-Z0-9]+)")


def _parse_hit(q: dict) -> dict:
    params = {k[3:] if k.startswith(("ep.", "up.")) else k[4:]: v for k, v in q.items() if k.startswith(("ep.", "epn.", "up."))}
    return {"measurement_id": q.get("tid"), "event": q.get("en"), "page": q.get("dl"), "params": params}


async def verify_site(url: str, wait_ms: int = 4000, click_selector: str | None = None) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"error": "Playwright is not installed. Run: pip install playwright && python -m playwright install chromium"}

    hits: list[dict] = []
    gtm_ids: set[str] = set()
    errors: list[str] = []

    async def on_collect(route, request):
        # Shared fields are in the URL; a batched POST carries one event per body line.
        base = dict(parse_qsl(urlparse(request.url).query, keep_blank_values=True))
        lines = [ln for ln in (request.post_data or "").splitlines() if ln.strip()]
        for line in lines or [""]:
            hit = _parse_hit({**base, **dict(parse_qsl(line, keep_blank_values=True))})
            if hit["event"]:
                hits.append(hit)
        await route.fulfill(status=204, body="")

    async with async_playwright() as p:
        # CHROMIUM_PATH lets you use an existing Chrome/Chromium instead of `playwright install chromium`.
        exe = os.environ.get("CHROMIUM_PATH") or None
        browser = await p.chromium.launch(headless=True, executable_path=exe, args=["--no-sandbox"] if exe else None)
        try:
            context = await browser.new_context()
            await context.route(re.compile(r".*/g/collect.*"), on_collect)
            page = await context.new_page()
            page.on("request", lambda r: gtm_ids.update(GTM_LOAD.findall(r.url)))
            page.on("pageerror", lambda e: errors.append(str(e)[:300]))
            resp = await page.goto(url, wait_until="load", timeout=45000)
            await page.wait_for_timeout(wait_ms)
            if click_selector:
                await page.click(click_selector, timeout=10000)
                await page.wait_for_timeout(wait_ms)
            data_layer = await page.evaluate(
                """() => (window.dataLayer || []).slice(0, 60).map(e => {
                    try { return JSON.parse(JSON.stringify(e, (k, v) => (v instanceof Element ? '[element]' : v))); }
                    catch (err) { return String(e); }
                })"""
            )
            loaded = await page.evaluate("() => Object.keys(window.google_tag_manager || {})")
            return {
                "url": url,
                "final_url": page.url,
                "http_status": resp.status if resp else None,
                "gtm_containers_requested": sorted(gtm_ids),
                "google_tag_manager_keys": loaded,
                "ga4_hits": hits,
                "data_layer": json.loads(json.dumps(data_layer, default=str))[:60],
                "page_errors": errors[:20],
                "note": "No GA4 hits usually means a consent banner is waiting for a choice, or the tags didn't fire."
                if not hits else None,
            }
        finally:
            await browser.close()
