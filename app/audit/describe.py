"""Human-readable descriptions of a GTM container for audit reports: friendly type names, trigger conditions
in plain words, where each item is used, and what each finding means."""

from __future__ import annotations

from collections import defaultdict

from .container import BUILT_IN_TRIGGERS, Container, map_value, param, var_refs

TAG_TYPES = {
    "googtag": "Google Tag", "gaawe": "GA4 Event", "gaawc": "GA4 Configuration (legacy)", "ua": "Universal Analytics (retired)",
    "html": "Custom HTML", "img": "Custom Image", "awct": "Google Ads Conversion", "sp": "Google Ads Remarketing",
    "gclidw": "Conversion Linker", "flc": "Floodlight Counter", "fls": "Floodlight Sales", "awud": "Google Ads User-Provided Data",
    "awcc": "Google Ads Calls from Website", "baut": "Microsoft Ads UET", "bzi": "LinkedIn Insight", "hjtc": "Hotjar",
    "pntr": "Pinterest", "twitter_website_tag": "Twitter/X", "cegg": "Crazy Egg", "mpm": "Mouseflow", "opt": "Google Optimize (retired)",
    "zone": "Zone",
}
TRIGGER_TYPES = {
    "pageview": "Page View", "domReady": "DOM Ready", "windowLoaded": "Window Loaded", "customEvent": "Custom Event",
    "click": "All Elements Click", "linkClick": "Just Links Click", "formSubmission": "Form Submission",
    "historyChange": "History Change", "timer": "Timer", "scrollDepth": "Scroll Depth", "youTubeVideo": "YouTube Video",
    "elementVisibility": "Element Visibility", "triggerGroup": "Trigger Group", "jsError": "JavaScript Error",
    "consentInit": "Consent Initialization", "init": "Initialization",
}
VARIABLE_TYPES = {
    "c": "Constant", "v": "Data Layer Variable", "jsm": "Custom JavaScript", "j": "JavaScript Variable", "k": "1st-Party Cookie",
    "u": "URL", "smm": "Lookup Table", "remm": "RegEx Table", "d": "DOM Element", "aev": "Auto-Event Variable",
    "gas": "Google Analytics Settings (UA)", "f": "HTTP Referrer", "e": "Custom Event", "ctv": "Container Version",
    "r": "Random Number", "uv": "Undefined Value", "cid": "Container ID", "dbg": "Debug Mode", "awec": "User-Provided Data",
    "gtcs": "Google Tag: Configuration Settings", "gtes": "Google Tag: Event Settings", "vis": "Element Visibility",
}
CONDITION_WORDS = {
    "equals": "equals", "contains": "contains", "startsWith": "starts with", "endsWith": "ends with",
    "matchRegex": "matches regex", "cssSelector": "matches CSS selector", "greater": "greater than", "greaterOrEquals": "at least",
    "less": "less than", "lessOrEquals": "at most",
}

CONSENT_WORDS = {"notSet": "Not set", "needed": "Requires consent", "notNeeded": "No consent needed"}

# Category, plain-language impact, and suggested owner for each rule.
RULE_INFO = {
    "GA4_NO_MEASUREMENT_ID": ("Data loss", "Events from this tag are not collected at all.", "Analytics"),
    "BROKEN_TRIGGER_REF": ("Data loss", "The tag can't fire on the trigger that was meant to fire it.", "Analytics"),
    "BROKEN_VARIABLE_REF": ("Data quality", "The value sent is 'undefined', so reports show blanks or (not set).", "Analytics"),
    "DUPLICATE_TAG": ("Data quality", "Every matching event is counted two or more times, inflating conversions and revenue.", "Analytics"),
    "DUPLICATE_GOOGLE_TAG": ("Data quality", "Page views and sessions can be double-counted.", "Analytics"),
    "GA4_EVENT_NO_NAME": ("Data loss", "GA4 drops events without a name.", "Analytics"),
    "GA4_EVENT_NAME_INVALID": ("Data loss", "GA4 rejects or silently ignores these events.", "Analytics"),
    "GA4_EVENT_NAME_STYLE": ("Data quality", "Mixed-case names split one action into several rows in reports.", "Analytics"),
    "GA4_TOO_MANY_PARAMS": ("Data loss", "Parameters beyond the 25th are dropped.", "Analytics"),
    "GA4_PARAM_NAME_INVALID": ("Data loss", "These parameters are dropped and can't be used as custom dimensions.", "Analytics"),
    "ECOMMERCE_NO_ITEMS": ("Data loss", "Ecommerce, item and revenue reports stay empty for this event.", "Analytics + Developers"),
    "LEGACY_UA_TAG": ("Performance", "Universal Analytics no longer processes data; the tag only slows pages down.", "Analytics"),
    "LEGACY_GA4_CONFIG": ("Maintenance", "No impact today; listed for awareness.", "Analytics"),
    "TAG_NO_TRIGGER": ("Maintenance", "The tag never fires. It may be leftover, or something is missing.", "Analytics"),
    "CUSTOM_HTML_RISK": ("Security & performance", "Custom code can slow or break pages and bypasses template permissions.", "Developers"),
    "CUSTOM_HTML_ALL_PAGES": ("Performance", "Runs extra script on every page load.", "Analytics + Developers"),
    "CONSENT_NOT_SET": ("Compliance", "The tag may fire before or without user consent (GDPR/ePrivacy risk).", "Analytics + Legal"),
    "MISSING_CONVERSION_LINKER": ("Data loss", "Google Ads conversions are under-reported, especially on Safari.", "Marketing"),
    "CATCH_ALL_TRIGGER": ("Data quality", "Tags fire on every internal GTM event, creating many unintended hits.", "Analytics"),
    "DUPLICATE_TRIGGER": ("Maintenance", "Harder to maintain; changes to one copy don't apply to the other.", "Analytics"),
    "HARDCODED_MEASUREMENT_ID": ("Maintenance", "Switching or adding a property means editing every tag by hand.", "Analytics"),
    "PAUSED_TAG": ("Maintenance", "Clutter; a paused tag can be re-enabled by mistake.", "Analytics"),
    "UNUSED_TRIGGER": ("Maintenance", "Clutter; makes the container harder to review.", "Analytics"),
    "UNUSED_VARIABLE": ("Maintenance", "Clutter; makes the container harder to review.", "Analytics"),
    "NAMING_CONVENTION": ("Maintenance", "Inconsistent names make the container slow to audit and easy to break.", "Analytics"),
    "NO_FOLDERS": ("Maintenance", "Large containers without folders are hard to navigate.", "Analytics"),
}


def _type_label(table: dict, t: str) -> str:
    if t.startswith("cvt_"):
        return f"Custom template ({t})"
    return table.get(t, t)


def _short(value, limit: int = 160) -> str:
    s = str(value).replace("\n", " ").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _condition_text(cond: dict) -> str:
    arg0 = next((p.get("value") for p in cond.get("parameter", []) if p.get("key") == "arg0"), "?")
    arg1 = next((p.get("value") for p in cond.get("parameter", []) if p.get("key") == "arg1"), "?")
    negate = any(p.get("key") == "negate" and str(p.get("value")).lower() == "true" for p in cond.get("parameter", []))
    word = CONDITION_WORDS.get(cond.get("type"), cond.get("type", "?"))
    return f"{arg0} {'does not ' + word if negate else word} {arg1}"


def trigger_conditions(t: dict) -> str:
    parts = []
    if t.get("type") == "customEvent":
        parts += [("Event: " + _condition_text(c)) for c in t.get("customEventFilter", []) or []]
    parts += [_condition_text(c) for c in (t.get("autoEventFilter") or []) + (t.get("filter") or [])]
    if t.get("type") == "triggerGroup":
        parts.append("All of trigger IDs " + ", ".join(i.get("value", "") for i in param(t, "triggerIds", []) or []))
    for key, label in (("eventName", "Event name"), ("interval", "Every (ms)"), ("verticalThresholdsPercent", "Scroll %"),
                       ("elementSelector", "Element"), ("selectorType", "Selector type")):
        v = param(t, key) if key != "eventName" else (t.get("eventName") or {}).get("value")
        if v:
            parts.append(f"{label}: {v}")
    return "; ".join(parts) or "All occurrences"


def tag_details(c: Container, t: dict) -> str:
    typ = t["type"]
    if typ == "googtag":
        return f"Tag ID: {param(t, 'tagId')}"
    if typ in ("gaawe", "gaawc"):
        bits = [f"Event name: {param(t, 'eventName')}" if typ == "gaawe" else None,
                f"Measurement ID: {param(t, 'measurementIdOverride') or param(t, 'measurementId') or '(missing)'}"]
        rows = param(t, "eventSettingsTable", []) or []
        if rows:
            bits.append("Parameters: " + ", ".join(f"{map_value(r, 'parameter')}={map_value(r, 'parameterValue')}" for r in rows))
        if str(param(t, "sendEcommerceData", "false")).lower() == "true":
            bits.append("Sends ecommerce data")
        return "; ".join(b for b in bits if b)
    if typ == "html":
        return "Code: " + _short(param(t, "html") or "", 200)
    if typ == "awct":
        return f"Conversion ID: {param(t, 'conversionId')}; Label: {param(t, 'conversionLabel')}"
    simple = [f"{p.get('key')}: {_short(p.get('value'), 60)}" for p in t.get("parameter", []) or [] if p.get("type") in ("template", "boolean", "integer")]
    return "; ".join(simple[:8])


def variable_details(v: dict) -> str:
    typ = v["type"]
    if typ == "c":
        return f"Value: {param(v, 'value')}"
    if typ == "v":
        return f"Data layer key: {param(v, 'name')}"
    if typ in ("jsm",):
        return "Code: " + _short(param(v, "javascript") or "", 160)
    if typ == "j":
        return f"Global variable: {param(v, 'name')}"
    if typ == "k":
        return f"Cookie: {param(v, 'name')}"
    if typ == "u":
        return f"URL part: {param(v, 'component') or 'full URL'}"
    if typ in ("smm", "remm"):
        return f"Input: {param(v, 'input')}; {len(param(v, 'map', []) or [])} rows"
    if typ == "d":
        return f"Element: {param(v, 'elementId') or param(v, 'selector')}"
    simple = [f"{p.get('key')}: {_short(p.get('value'), 60)}" for p in v.get("parameter", []) or [] if p.get("type") == "template"]
    return "; ".join(simple[:5])


def _folder_names(c: Container) -> dict:
    return {f.get("folderId"): f.get("name") for f in c.folders}


def inventory(c: Container, findings: list[dict]) -> dict:
    """Tags, triggers and variables with plain-language details, usage and how many findings touch each."""
    issues = defaultdict(list)
    for f in findings:
        for i in f.get("entity_ids") or []:
            issues[(f.get("entity_kind"), i)].append(f"{f['severity']}: {f['title']}")
    folders = _folder_names(c)
    tags_by_trigger, users_by_var = defaultdict(list), defaultdict(list)
    for t in c.tags:
        for i in (t.get("firingTriggerId") or []) + (t.get("blockingTriggerId") or []):
            tags_by_trigger[i].append(t["name"])
    for kind, items in (("Tag", c.tags), ("Trigger", c.triggers), ("Variable", c.variables)):
        for e in items:
            for r in var_refs(e):
                if r != e.get("name"):
                    users_by_var[r].append(f"{kind}: {e['name']}")

    tags = [{
        "id": t["tagId"], "name": t["name"], "type": _type_label(TAG_TYPES, t["type"]),
        "status": "Paused" if t.get("paused") else "Active",
        "fires_on": [c.trigger_name(i) for i in t.get("firingTriggerId") or []],
        "blocked_by": [c.trigger_name(i) for i in t.get("blockingTriggerId") or []],
        "consent": CONSENT_WORDS.get((t.get("consentSettings") or {}).get("consentStatus", "notSet"), "Not set"),
        "details": tag_details(c, t), "folder": folders.get(t.get("parentFolderId"), ""),
        "issues": issues.get(("tag", t["tagId"]), []),
    } for t in c.tags]
    triggers = [{
        "id": t["triggerId"], "name": t["name"], "type": _type_label(TRIGGER_TYPES, t["type"]),
        "conditions": trigger_conditions(t), "used_by": tags_by_trigger.get(t["triggerId"], []),
        "folder": folders.get(t.get("parentFolderId"), ""), "issues": issues.get(("trigger", t["triggerId"]), []),
    } for t in c.triggers]
    variables = [{
        "id": v["variableId"], "name": v["name"], "type": _type_label(VARIABLE_TYPES, v["type"]),
        "details": variable_details(v), "used_by": users_by_var.get(v["name"], []),
        "folder": folders.get(v.get("parentFolderId"), ""), "issues": issues.get(("variable", v["variableId"]), []),
    } for v in c.variables]
    return {"tags": tags, "triggers": triggers, "variables": variables,
            "built_in_variables": sorted(n for n in c.builtin_names if n),
            "built_in_triggers_used": sorted({BUILT_IN_TRIGGERS[i] for t in c.tags for i in t.get("firingTriggerId") or [] if i in BUILT_IN_TRIGGERS})}


def enrich_findings(c: Container, findings: list[dict]) -> list[dict]:
    """Adds category, impact, owner and a full description of every affected item to each finding."""
    out = []
    for f in findings:
        category, impact, owner = RULE_INFO.get(f["rule"], ("Other", "", "Analytics"))
        affected = []
        for i in f.get("entity_ids") or []:
            kind = f.get("entity_kind")
            e = c.entity(kind, i) if kind else None
            if not e:
                continue
            if kind == "tag":
                affected.append({"kind": "Tag", "id": i, "name": e["name"], "type": _type_label(TAG_TYPES, e["type"]),
                                 "status": "Paused" if e.get("paused") else "Active",
                                 "triggers": ", ".join(c.trigger_name(x) for x in e.get("firingTriggerId") or []) or "(none)",
                                 "details": tag_details(c, e)})
            elif kind == "trigger":
                affected.append({"kind": "Trigger", "id": i, "name": e["name"], "type": _type_label(TRIGGER_TYPES, e["type"]),
                                 "status": "", "triggers": "", "details": trigger_conditions(e)})
            else:
                affected.append({"kind": "Variable", "id": i, "name": e["name"], "type": _type_label(VARIABLE_TYPES, e["type"]),
                                 "status": "", "triggers": "", "details": variable_details(e)})
        out.append({**f, "category": category, "impact": impact, "owner": owner, "affected": affected})
    return out
