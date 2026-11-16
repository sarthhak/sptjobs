"""Consent analysis for GTM containers: which GTM event each trigger listens to, whether exceptions can apply,
which tags are consent infrastructure, which vendors are present, and a tag x trigger consent matrix."""

from __future__ import annotations

import re

from .container import ALL_PAGES, CONSENT_INIT, INITIALIZATION, Container, param, var_refs

TYPE_EVENT = {
    "pageview": "gtm.js", "domReady": "gtm.dom", "windowLoaded": "gtm.load", "click": "gtm.click", "linkClick": "gtm.linkClick",
    "formSubmission": "gtm.formSubmit", "historyChange": "gtm.historyChange", "timer": "gtm.timer", "scrollDepth": "gtm.scrollDepth",
    "elementVisibility": "gtm.elementVisibility", "youTubeVideo": "gtm.video", "jsError": "gtm.pageError",
    "consentInit": "gtm.init_consent", "init": "gtm.init",
}
BUILT_IN_EVENT = {ALL_PAGES: "gtm.js", INITIALIZATION: "gtm.init", CONSENT_INIT: "gtm.init_consent"}

CONSENT_WORDS = re.compile(r"consent|gdpr|privacy|cookie.?pref|permit|optanon|cookiebot|truste|trustarc|cmapi|usercentrics|didomi|"
                           r"notice_(gdpr|behavior)|cookieyes|iubenda|osano", re.I)
CMP_WORDS = re.compile(r"trustarc|truste|onetrust|optanon|cookiebot|usercentrics|didomi|cookieyes|iubenda|osano|termly|cookie ?law|"
                       r"consent|cookie.?(removal|banner|wipe|delet)|\bcmp\b|gdpr", re.I)
CMP_SCRIPT = re.compile(r"consent\.trustarc\.com|consent-pref\.trustarc|cdn\.cookielaw\.org|optanon|consent\.cookiebot\.com|"
                        r"app\.usercentrics\.eu|sdk\.privacy-center\.org|cdn-cookieyes\.com|cdn\.iubenda\.com|cmp\.osano\.com", re.I)
CONSENT_MODE_CODE = re.compile(r"""gtag\(\s*['"]consent['"]|['"]consent['"]\s*,\s*['"](default|update)['"]""", re.I)

# (vendor, regex on html/template, advertising?)
VENDORS = [
    ("Meta Pixel", r"fbq\(|connect\.facebook\.net|facebook-domain-verification", True),
    ("Microsoft UET", r"bat\.bing\.com|uetq", True), ("LinkedIn Insight", r"snap\.licdn\.com|_linkedin_partner_id", True),
    ("TikTok Pixel", r"analytics\.tiktok\.com|ttq\.", True), ("StackAdapt", r"stackadapt|saq\(", True),
    ("Quantcast", r"quantserve|quantcount|_qevents|qcSegs", True), ("Pinterest", r"pintrk|s\.pinimg\.com", True),
    ("Twitter/X", r"static\.ads-twitter\.com|twq\(", True), ("Snapchat", r"snaptr|sc-static\.net", True),
    ("Criteo", r"criteo", True), ("Taboola", r"taboola", True), ("Outbrain", r"outbrain", True),
    ("Google Ads (custom code)", r"googleadservices|['\"]AW-\d+", True), ("DoubleClick/Floodlight", r"doubleclick\.net|fls\.doubleclick", True),
    ("Hotjar", r"hotjar", False), ("Microsoft Clarity", r"clarity\.ms", False), ("Crazy Egg", r"crazyegg", False),
    ("Mouseflow", r"mouseflow", False), ("VWO", r"visualwebsiteoptimizer|vwo_", False), ("Optimizely", r"optimizely", False),
    ("Adobe", r"adobedtm|appmeasurement|omniture", False), ("HubSpot", r"hs-scripts|hsforms|hubspot", False),
    ("Marketo", r"munchkin|marketo", False), ("Salesforce", r"salesforce|force\.com|embeddedservice|agentforce", False),
    ("Lead Anywhere", r"chatanexpert|leadanywhere", False), ("Intercom", r"intercom", False), ("Drift", r"drift\.com|driftt", False),
    ("Zendesk", r"zdassets|zendesk", False), ("TrustArc", r"trustarc|truste", False), ("OneTrust", r"onetrust|cookielaw|optanon", False),
    ("Cookiebot", r"cookiebot", False),
]
TYPE_VENDOR = {"baut": ("Microsoft UET", True), "bzi": ("LinkedIn Insight", True), "pntr": ("Pinterest", True),
               "twitter_website_tag": ("Twitter/X", True), "hjtc": ("Hotjar", False), "cegg": ("Crazy Egg", False),
               "mpm": ("Mouseflow", False), "awct": ("Google Ads", True), "sp": ("Google Ads", True), "flc": ("Floodlight", True),
               "fls": ("Floodlight", True), "gclidw": ("Google Ads", True), "awud": ("Google Ads", True)}
GOOGLE_TYPES = {"googtag", "gaawe", "gaawc", "awct", "sp", "gclidw", "flc", "fls", "ua", "awcc", "awud"}


def template_names(c: Container) -> dict[str, str]:
    """Maps tag type (cvt_<container>_<template>) to the custom template's name."""
    out = {}
    for t in c.raw.get("customTemplate", []) or []:
        tid = str(t.get("templateId", ""))
        for tag in c.tags:
            if tag["type"].startswith("cvt_") and tag["type"].endswith("_" + tid):
                out[tag["type"]] = t.get("name", "")
    return out


def tag_text(c: Container, tag: dict, templates: dict) -> str:
    return " ".join([tag.get("name", ""), param(tag, "html") or "", param(tag, "url") or "", templates.get(tag["type"], "")])


def vendor(c: Container, tag: dict, templates: dict) -> tuple[str, bool] | None:
    if tag["type"] in TYPE_VENDOR:
        return TYPE_VENDOR[tag["type"]]
    text = tag_text(c, tag, templates)
    for name, rx, ad in VENDORS:
        if re.search(rx, text, re.I):
            return name, ad
    return None


def is_consent_infrastructure(c: Container, tag: dict, templates: dict) -> bool:
    return bool(CMP_WORDS.search(tag.get("name", "")) or CMP_SCRIPT.search(param(tag, "html") or "")
                or CMP_WORDS.search(templates.get(tag["type"], "")))


def consent_variables(c: Container) -> set[str]:
    names = set()
    for v in c.variables:
        text = v["name"] + " " + " ".join(str(p.get("value", "")) for p in v.get("parameter", []) or [])
        if CONSENT_WORDS.search(text):
            names.add(v["name"])
    return names


def trigger_event(c: Container, trigger_id: str) -> tuple[str, str | None]:
    """('event', name) | ('regex', pattern) | ('unknown', None)."""
    if trigger_id in BUILT_IN_EVENT:
        return "event", BUILT_IN_EVENT[trigger_id]
    t = c.trigger_by_id.get(trigger_id)
    if not t:
        return "unknown", None
    if t["type"] in TYPE_EVENT:
        return "event", TYPE_EVENT[t["type"]]
    if t["type"] == "customEvent":
        for cond in t.get("customEventFilter", []) or []:
            arg1 = next((p.get("value") for p in cond.get("parameter", []) if p.get("key") == "arg1"), None)
            if arg1 is None:
                continue
            if cond.get("type") == "equals":
                return "event", arg1
            if cond.get("type") == "matchRegex":
                return "regex", arg1
        return "unknown", None
    return "unknown", None


def event_label(kind: str, value: str | None) -> str:
    return value if kind == "event" else (f"regex {value}" if kind == "regex" else "?")


def exception_applies(fire: tuple, block: tuple) -> bool | None:
    fk, fv = fire
    bk, bv = block
    if fk == "unknown" or bk == "unknown":
        return None
    if bk == "regex":
        if fk == "event":
            try:
                return bool(re.search(bv, fv))
            except re.error:
                return None
        return None
    if fk == "regex":
        return None
    return fv == bv


def references_consent(c: Container, trigger_id: str, consent_vars: set[str]) -> bool:
    t = c.trigger_by_id.get(trigger_id)
    return bool(t and var_refs(t) & consent_vars)


def consent_matrix(c: Container) -> list[dict]:
    templates = template_names(c)
    cvars = consent_variables(c)
    rows = []
    for tag in c.tags:
        if tag.get("paused"):
            continue
        infra = is_consent_infrastructure(c, tag, templates)
        settings_needed = (tag.get("consentSettings") or {}).get("consentStatus") == "needed"
        blocks = tag.get("blockingTriggerId") or []
        v = vendor(c, tag, templates)
        for fid in tag.get("firingTriggerId") or []:
            fire = trigger_event(c, fid)
            results = [exception_applies(fire, trigger_event(c, b)) for b in blocks]
            if not blocks:
                status = "No exceptions"
            elif any(r is True for r in results):
                status = "Effective"
            elif all(r is False for r in results):
                status = "Ineffective (event mismatch)"
            else:
                status = "Unclear"
            consent_blocks = any(r is True and references_consent(c, b, cvars) for r, b in zip(results, blocks))
            self_gated = references_consent(c, fid, cvars)
            if infra:
                risk = "Consent tool (review)"
            elif tag["type"] in GOOGLE_TYPES:
                risk = "Google built-in consent checks"
            elif settings_needed or self_gated or consent_blocks:
                risk = "OK"
            else:
                risk = "No consent check"
            rows.append({
                "tag_id": tag["tagId"], "tag": tag["name"], "type": tag["type"], "vendor": v[0] if v else "",
                "advertising": bool(v and v[1]), "firing_trigger": c.trigger_name(fid), "firing_trigger_id": fid,
                "firing_event": event_label(*fire),
                "exception_events": "; ".join(sorted({event_label(*trigger_event(c, b)) for b in blocks})) or "-",
                "exception_status": status, "self_gated": self_gated, "consent_settings": settings_needed, "risk": risk,
                "consent_infrastructure": infra,
            })
    return rows


def consent_mode_status(c: Container) -> dict:
    templates = template_names(c)
    live, paused = [], []
    for tag in c.tags:
        is_cm = (CONSENT_MODE_CODE.search(param(tag, "html") or "")
                 or (tag["type"].startswith("cvt_") and re.search(r"consent|cmp|cookiebot|onetrust|usercentrics|trustarc|didomi",
                                                                   templates.get(tag["type"], ""), re.I)))
        if is_cm:
            (paused if tag.get("paused") else live).append(tag["name"])
    return {"live": live, "paused": paused}


def third_party_live(c: Container) -> list[str]:
    templates = template_names(c)
    names = []
    for tag in c.tags:
        if tag.get("paused"):
            continue
        v = vendor(c, tag, templates)
        if v and v[0] not in names and not v[0].startswith("Google"):
            names.append(v[0])
    return names
