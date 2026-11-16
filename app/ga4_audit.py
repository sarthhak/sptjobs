"""GA4 property audit: configuration checks (Admin API) plus data-quality checks on the last 28 days (Data API).

Each check is best-effort: if one query fails, the audit notes it and carries on.
"""

from __future__ import annotations

import re
from collections import defaultdict
from urllib.parse import urlparse

from .audit.rules import GA4_NAME, RESERVED_EVENTS, SEVERITY_ORDER, SEVERITY_WEIGHT

PERIOD = {"start_date": "28daysAgo", "end_date": "yesterday"}
PAYMENT_GATEWAYS = ("paypal", "stripe", "razorpay", "payu", "adyen", "checkout.com", "klarna", "worldpay", "braintree",
                    "authorize.net", "ccavenue", "paytm", "mollie", "sagepay", "opayo", "3dsecure", "secure.")
PII_REGEX = r".*(@|%40|[?&](e-?mail|email_address|phone|mobile|tel|password|pwd|first_?name|last_?name|address)=).*"
GA4_LIMITS = {"event_custom_dimensions": 50, "user_custom_dimensions": 25, "custom_metrics": 50, "event_names": 500}


def _finding(rule, severity, title, detail, recommendation, category, impact, owner="Analytics", evidence=None):
    return {"id": rule, "rule": rule, "severity": severity, "title": title, "detail": detail, "recommendation": recommendation,
            "category": category, "impact": impact, "owner": owner, "evidence": evidence or []}


def _mask_url(url: str) -> str:
    url = re.sub(r"[^\s/?&=@]+(@|%40)[^\s/?&=]+", "***@***", url)
    return re.sub(r"([?&](?:e-?mail|email_address|phone|mobile|tel|password|pwd|first_?name|last_?name|address)=)[^&]*", r"\1***", url, flags=re.I)


def _share(part: float, total: float) -> float:
    return round(100 * part / total, 1) if total else 0.0


def run_ga4_audit(ga4, prop: str) -> dict:
    findings: list[dict] = []
    errors: dict[str, str] = {}
    warnings: set[str] = set()

    def report(name: str, spec: dict) -> dict | None:
        try:
            data = ga4.run_report(prop, {**PERIOD, **spec})
            warnings.update(data.get("warnings") or [])
            return data
        except Exception as e:  # noqa: BLE001
            errors[name] = str(e)[:300]
            return None

    cfg = ga4.property_config(prop)
    errors.update({f"settings: {k}": v for k, v in (cfg.get("errors") or {}).items()})
    p = cfg.get("property") or {}
    streams = cfg.get("data_streams") or []
    web_streams = [s for s in streams if s.get("type") == "WEB_DATA_STREAM"]
    key_events = [k.get("eventName") for k in cfg.get("key_events") or [] if k.get("eventName")]
    cdims = cfg.get("custom_dimensions") or []

    # ---- configuration ------------------------------------------------------------
    retention = (cfg.get("data_retention") or {}).get("eventDataRetention")
    if retention == "TWO_MONTHS":
        findings.append(_finding("RETENTION_2_MONTHS", "high", "Event data retention is 2 months",
            "Explorations and funnel reports can only look back 2 months.",
            "Admin > Data collection and modification > Data retention: set Event data to 14 months (or 50 with GA4 360).",
            "Configuration", "Year-over-year and long-range analysis in Explorations is impossible."))
    if "key_events" in cfg and not key_events:
        findings.append(_finding("NO_KEY_EVENTS", "high", "No key events are defined",
            "No events are marked as key events (formerly conversions).",
            "Admin > Events: mark your important actions (purchase, generate_lead, sign_up...) as key events.",
            "Configuration", "Conversion reporting, attribution and Google Ads bidding have nothing to optimise for.", "Analytics + Marketing"))
    if "data_streams" in cfg and not web_streams:
        findings.append(_finding("NO_WEB_STREAM", "medium", "No web data stream", "The property has no web data stream.",
            "Create a web stream if this property should measure a website.", "Configuration", "No website data is collected."))
    for name, em in (cfg.get("enhanced_measurement") or {}).items():
        if not em.get("streamEnabled"):
            findings.append(_finding("ENHANCED_MEASUREMENT_OFF", "medium", "Enhanced measurement is off",
                f"Stream {name} has enhanced measurement disabled.",
                "Turn it on (Admin > Data streams > your stream) unless you track scrolls, outbound clicks, site search, video and downloads yourself.",
                "Configuration", "Scrolls, outbound clicks, site search, file downloads and video engagement are not measured."))
        else:
            off = [label for key, label in (("scrollsEnabled", "scrolls"), ("outboundClicksEnabled", "outbound clicks"),
                                            ("siteSearchEnabled", "site search"), ("fileDownloadsEnabled", "file downloads"),
                                            ("formInteractionsEnabled", "form interactions"), ("videoEngagementEnabled", "video"))
                   if em.get(key) is False]
            if off:
                findings.append(_finding("ENHANCED_MEASUREMENT_PARTIAL", "info", "Some enhanced measurement events are off",
                    f"Off on {name}: {', '.join(off)}.", "Check these are tracked another way, or switch them on.",
                    "Configuration", "Those interactions may not be measured."))
    if "custom_dimensions" in cfg and not cdims:
        findings.append(_finding("NO_CUSTOM_DIMENSIONS", "low", "No custom dimensions are registered",
            "Event parameters you send beyond the automatic ones can't be used in reports until registered.",
            "Admin > Custom definitions: register the parameters you report on.", "Configuration",
            "Custom parameters are collected but invisible in standard reports."))
    event_scoped = [d for d in cdims if d.get("scope") == "EVENT"]
    if len(event_scoped) >= GA4_LIMITS["event_custom_dimensions"] - 5:
        findings.append(_finding("CUSTOM_DIMENSION_LIMIT", "medium", f"{len(event_scoped)} of 50 event-scoped custom dimensions used",
            "You are close to the standard-property limit.", "Archive dimensions nobody uses.", "Configuration",
            "New parameters can't be registered once the limit is reached."))
    if p and not p.get("currencyCode"):
        findings.append(_finding("NO_CURRENCY", "low", "No reporting currency set", "The property has no currency.",
            "Admin > Property details: set the currency you report revenue in.", "Configuration", "Revenue may be reported inconsistently."))
    if "google_ads_links" in cfg and not cfg.get("google_ads_links"):
        findings.append(_finding("NO_ADS_LINK", "info", "Google Ads is not linked", "No Google Ads account is linked.",
            "Link Google Ads if you run Ads, to import key events and share audiences.", "Configuration",
            "Ads can't use GA4 key events or audiences.", "Marketing"))

    # ---- data quality (last 28 days) ----------------------------------------------
    overview = report("overview", {"metrics": ["sessions", "totalUsers", "engagementRate", "keyEvents", "eventCount"]})
    totals = overview["rows"][0] if overview and overview["rows"] else {}
    sessions = totals.get("sessions") or 0
    if overview is not None and not sessions:
        findings.append(_finding("NO_DATA", "critical", "No sessions recorded in the last 28 days",
            "The property received no traffic data.", "Check the Google tag is on the site and publishing to this property.",
            "Data loss", "Nothing is being measured."))
    eng = totals.get("engagementRate")
    if sessions and isinstance(eng, (int, float)) and eng < 0.2:
        findings.append(_finding("LOW_ENGAGEMENT_RATE", "medium", f"Engagement rate is only {eng:.0%}",
            "Unusually low engagement often points to tracking problems (sessions without page views, bot traffic, double tagging).",
            "Check the Google tag fires once per page and that bots/internal traffic are filtered.", "Data quality",
            "Engagement-based metrics may be unreliable."))

    channels = report("channels", {"dimensions": ["sessionDefaultChannelGroup"], "metrics": ["sessions"], "limit": 50})
    if channels and sessions:
        unassigned = sum(r["sessions"] for r in channels["rows"] if r["sessionDefaultChannelGroup"] in ("Unassigned", "(not set)"))
        share = _share(unassigned, sessions)
        if share > 5:
            findings.append(_finding("UNASSIGNED_TRAFFIC", "high" if share > 15 else "medium", f"{share}% of sessions are Unassigned",
                f"{unassigned:,} of {sessions:,} sessions have no recognised channel.",
                "Use standard utm_medium values (cpc, email, social, referral...), tag every campaign link, and check for sessions that start without a page_view.",
                "Data quality", "Channel performance and marketing ROI are understated for the channels that drove this traffic.", "Marketing + Analytics"))

    sources = report("sources", {"dimensions": ["sessionSource", "sessionMedium"], "metrics": ["sessions"], "limit": 500,
                                 "order_by": {"field": "sessions", "desc": True}})
    if sources and sessions:
        hosts = set()
        for s in web_streams:
            host = urlparse((s.get("webStreamData") or {}).get("defaultUri", "")).hostname or ""
            if host:
                hosts.add(host.removeprefix("www."))
        self_ref = [r for r in sources["rows"] if r["sessionMedium"] == "referral" and any(h and h in r["sessionSource"] for h in hosts)]
        if self_ref:
            n = sum(r["sessions"] for r in self_ref)
            findings.append(_finding("SELF_REFERRALS", "high", f"Your own domain appears as a referral ({_share(n, sessions)}% of sessions)",
                "Sessions are restarted when visitors move between your own pages or subdomains.",
                "Configure cross-domain measurement for all your domains (Admin > Data streams > Configure tag settings) and make sure every page has the same Google tag.",
                "Data quality", "Sessions are split and the original source (e.g. paid search) is lost, so marketing is under-credited.",
                evidence=[f"{r['sessionSource']} / {r['sessionMedium']}: {r['sessions']:,} sessions" for r in self_ref[:10]]))
        gateways = [r for r in sources["rows"] if r["sessionMedium"] == "referral" and any(g in r["sessionSource"].lower() for g in PAYMENT_GATEWAYS)]
        if gateways:
            findings.append(_finding("PAYMENT_REFERRALS", "high", "Payment providers appear as referral sources",
                "Visitors returning from a payment page start a new session credited to the payment provider.",
                "Add these domains under Admin > Data streams > Configure tag settings > List unwanted referrals.",
                "Data quality", "Purchases are credited to the payment provider instead of the real marketing channel.",
                evidence=[f"{r['sessionSource']}: {r['sessions']:,} sessions" for r in gateways[:10]]))
        not_set = sum(r["sessions"] for r in sources["rows"] if r["sessionSource"] == "(not set)")
        if _share(not_set, sessions) > 5:
            findings.append(_finding("SOURCE_NOT_SET", "medium", f"{_share(not_set, sessions)}% of sessions have source (not set)",
                "Usually caused by events sent before page_view, Measurement Protocol hits without session data, or consent mode modelling.",
                "Make sure the Google tag fires on Initialization/All Pages before event tags.", "Data quality",
                "Traffic can't be attributed to a source."))

    landing = report("landing", {"dimensions": ["landingPage"], "metrics": ["sessions"], "limit": 1000})
    if landing and sessions:
        ns = sum(r["sessions"] for r in landing["rows"] if r["landingPage"] == "(not set)")
        if _share(ns, sessions) > 5:
            findings.append(_finding("LANDING_NOT_SET", "medium", f"{_share(ns, sessions)}% of sessions have landing page (not set)",
                "Sessions started without a page_view, typically because an event fired before the Google tag, or a session timed out and restarted on an event.",
                "Fire the Google tag on Initialization - All Pages and event tags after it.", "Data quality",
                "Landing-page reports undercount entry pages."))

    pii = report("pii", {"dimensions": ["pageLocation"], "metrics": ["screenPageViews"], "limit": 50,
                         "filters": [{"field": "pageLocation", "op": "regex", "value": PII_REGEX}]})
    if pii and pii["rows"]:
        findings.append(_finding("PII_IN_URLS", "critical", f"Possible personal data in page URLs ({len(pii['rows'])} URLs)",
            "URLs sent to GA4 contain what looks like email addresses or personal fields. Sending PII violates Google's terms.",
            "Stop forms from putting personal data in URLs (use POST), and redact those query parameters (Admin > Data streams > Redact data, or in GTM). Ask Google to delete the affected data if needed.",
            "Compliance", "Breach of Google Analytics terms and possibly of privacy law; Google can delete the property data.",
            "Developers + Legal", evidence=[_mask_url(r["pageLocation"]) for r in pii["rows"][:10]]))

    events = report("events", {"dimensions": ["eventName"], "metrics": ["eventCount"], "limit": 1000,
                               "order_by": {"field": "eventCount", "desc": True}})
    seen_events = {r["eventName"]: r["eventCount"] for r in (events or {}).get("rows", [])}
    if events:
        groups = defaultdict(list)
        for name in seen_events:
            groups[re.sub(r"[^a-z0-9]", "", name.lower())].append(name)
        dupes = [names for names in groups.values() if len(names) > 1]
        if dupes:
            findings.append(_finding("EVENT_NAME_VARIANTS", "medium", f"{len(dupes)} events are collected under several spellings",
                "The same action arrives with different capitalisation or separators, so reports split it.",
                "Pick one snake_case name per action and fix the tags sending the others.", "Data quality",
                "Totals for these actions are split across rows and easy to under-report.",
                evidence=[" / ".join(f"{n} ({seen_events[n]:,})" for n in names) for names in dupes[:15]]))
        styled = [n for n in seen_events if n != n.lower() or not GA4_NAME.match(n)]
        if styled:
            findings.append(_finding("EVENT_NAME_STYLE", "low", f"{len(styled)} event names aren't lowercase snake_case",
                "GA4 is case-sensitive and recommends snake_case.", "Use snake_case for new events.", "Maintenance",
                "Inconsistent names are harder to report on.", evidence=styled[:20]))
        if len(seen_events) > 400:
            findings.append(_finding("EVENT_NAME_LIMIT", "medium", f"{len(seen_events)} distinct event names in 28 days",
                "Standard properties allow 500 distinct event names per app/stream.",
                "Use event parameters instead of encoding details in event names.", "Data loss",
                "Once the limit is reached, new event names are not collected."))

    if key_events and events is not None:
        silent = [k for k in key_events if not seen_events.get(k)]
        if silent:
            findings.append(_finding("KEY_EVENTS_NOT_FIRING", "high", f"{len(silent)} key events recorded nothing in 28 days",
                "These are marked as key events but no matching events were collected.",
                "Check the tags that should send them, or unmark key events that are no longer used.", "Data loss",
                "Conversion reporting and Ads bidding miss these conversions.", "Analytics + Marketing", evidence=silent))

    if seen_events.get("purchase"):
        rev = report("revenue", {"metrics": ["ecommercePurchases", "purchaseRevenue"]})
        rrow = rev["rows"][0] if rev and rev["rows"] else {}
        if rrow and not rrow.get("purchaseRevenue"):
            findings.append(_finding("PURCHASE_NO_REVENUE", "high", "Purchases are tracked but revenue is 0",
                f"{seen_events['purchase']:,} purchase events but no purchase revenue.",
                "Send value and currency (and the items array) with the purchase event.", "Data loss",
                "Revenue, ROAS and item reports are empty.", "Analytics + Developers"))
        tx = report("transactions", {"dimensions": ["transactionId"], "metrics": ["eventCount"], "limit": 200,
                                     "filters": [{"field": "eventName", "op": "equals", "value": "purchase"}],
                                     "order_by": {"field": "eventCount", "desc": True}})
        if tx:
            dup = [r for r in tx["rows"] if r["transactionId"] not in ("(not set)", "") and r["eventCount"] > 1]
            missing = sum(r["eventCount"] for r in tx["rows"] if r["transactionId"] in ("(not set)", ""))
            if dup:
                findings.append(_finding("DUPLICATE_TRANSACTIONS", "high", f"{len(dup)}+ transaction IDs were recorded more than once",
                    "The same order is sent several times, typically when the thank-you page is reloaded or revisited.",
                    "Fire the purchase event only once per order (e.g. set a cookie or check order status server-side).",
                    "Data quality", "Revenue and purchase counts are inflated.", "Analytics + Developers",
                    evidence=[f"{r['transactionId']}: {r['eventCount']} times" for r in dup[:10]]))
            if missing:
                findings.append(_finding("TRANSACTION_ID_MISSING", "medium", f"{missing:,} purchases have no transaction ID",
                    "Without transaction_id GA4 can't de-duplicate purchases.", "Send transaction_id with every purchase event.",
                    "Data quality", "Duplicate purchases can't be detected or removed.", "Developers"))

    if any("thresholding" in w for w in warnings):
        findings.append(_finding("THRESHOLDING", "info", "Reports are subject to data thresholding",
            "GA4 hides low counts when Google signals is on, which can blank out rows in reports and the API.",
            "Consider Reporting identity 'Device-based' (Admin > Reporting identity) for complete numbers.",
            "Data quality", "Some rows and totals are withheld."))

    findings.sort(key=lambda f: SEVERITY_ORDER.index(f["severity"]))
    score = max(0, 100 - sum(SEVERITY_WEIGHT[f["severity"]] for f in findings))
    settings = {
        "Property": f"{p.get('displayName', '')} ({prop})",
        "Time zone": p.get("timeZone", ""), "Currency": p.get("currencyCode", ""), "Industry": p.get("industryCategory", ""),
        "Service level": p.get("serviceLevel", ""), "Event data retention": retention or "",
        "Reset retention on new activity": (cfg.get("data_retention") or {}).get("resetUserDataOnNewActivity", ""),
        "Data streams": "; ".join(f"{s.get('displayName')} ({(s.get('webStreamData') or {}).get('measurementId', s.get('type'))}"
                                  f"{', ' + (s.get('webStreamData') or {}).get('defaultUri', '') if s.get('webStreamData') else ''})" for s in streams),
        "Key events": ", ".join(key_events),
        "Custom dimensions": ", ".join(f"{d.get('displayName')} [{d.get('parameterName')}, {d.get('scope', '').lower()}]" for d in cdims),
        "Custom metrics": ", ".join(m.get("displayName", "") for m in cfg.get("custom_metrics") or []),
        "Google Ads links": str(len(cfg.get("google_ads_links") or [])),
    }
    return {
        "property": prop, "property_name": p.get("displayName") or prop, "period": "Last 28 days (to yesterday)",
        "score": score, "counts": {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITY_ORDER},
        "findings": findings, "settings": settings,
        "overview": {k: totals.get(k) for k in ("sessions", "totalUsers", "engagementRate", "keyEvents", "eventCount")},
        "channels": (channels or {}).get("rows", [])[:20],
        "top_events": (events or {}).get("rows", [])[:50],
        "errors": errors,
    }
