"""GTM audit rules. Each rule reads a Container and returns Findings. Rules never change anything."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from .container import ALL_PAGES, Container, map_value, param, signature, var_refs

SEVERITY_WEIGHT = {"critical": 15, "high": 8, "medium": 3, "low": 1, "info": 0}
SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]

GA4_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
RESERVED_PREFIXES = ("_", "firebase_", "ga_", "google_", "gtag.")
RESERVED_EVENTS = {
    "ad_activeview", "ad_click", "ad_exposure", "ad_impression", "ad_query", "ad_reward", "adunit_exposure",
    "app_background", "app_clear_data", "app_exception", "app_remove", "app_store_refund",
    "app_store_subscription_cancel", "app_store_subscription_convert", "app_store_subscription_renew",
    "app_uninstall", "app_update", "app_upgrade", "dynamic_link_app_open", "dynamic_link_app_update",
    "dynamic_link_first_open", "error", "firebase_campaign", "first_open", "first_visit", "in_app_purchase",
    "notification_dismiss", "notification_foreground", "notification_open", "notification_receive",
    "notification_send", "os_update", "session_start", "screen_view", "user_engagement",
}
ECOMMERCE_EVENTS = {
    "purchase", "refund", "add_to_cart", "remove_from_cart", "begin_checkout", "add_payment_info",
    "add_shipping_info", "view_item", "view_item_list", "select_item", "view_cart", "add_to_wishlist",
}
MEASUREMENT_ID = re.compile(r"^G-[A-Z0-9]{4,}$")
GOOGLE_TAG_TYPES = {"googtag", "gaawe", "gaawc", "awct", "sp", "gclidw", "flc", "fls", "ua", "awcc", "awud"}


@dataclass
class Finding:
    id: str
    rule: str
    severity: str
    title: str
    detail: str
    recommendation: str
    entity_kind: str | None = None
    entity_ids: list[str] = field(default_factory=list)
    entity_names: list[str] = field(default_factory=list)
    fixable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _f(rule, severity, title, detail, recommendation, kind=None, ents=(), fixable=False, suffix=None) -> Finding:
    ents = list(ents)
    ids = [e.get("tagId") or e.get("triggerId") or e.get("variableId") for e in ents]
    key = suffix or ",".join(ids) or "container"
    return Finding(
        id=f"{rule}:{key}", rule=rule, severity=severity, title=title, detail=detail,
        recommendation=recommendation, entity_kind=kind, entity_ids=ids,
        entity_names=[e.get("name", "") for e in ents], fixable=fixable,
    )


def _event_name(tag: dict) -> str | None:
    name = param(tag, "eventName")
    return name if isinstance(name, str) else None


def _event_params(tag: dict) -> list[str]:
    names = []
    for row in param(tag, "eventSettingsTable", []) or []:
        names.append(map_value(row, "parameter"))
    for row in param(tag, "eventParameters", []) or []:
        names.append(map_value(row, "name"))
    return [n for n in names if n]


def _ga4_name_problem(name: str) -> str | None:
    if "{{" in name:
        return None  # dynamic - checked at runtime only
    if not GA4_NAME.match(name):
        return "must start with a letter, use only letters, numbers and underscores, and be at most 40 characters"
    if name.lower().startswith(RESERVED_PREFIXES):
        return "uses a reserved prefix (google_, ga_, firebase_, _)"
    if name in RESERVED_EVENTS:
        return "is a name reserved by GA4"
    return None


def sanitize_event_name(name: str) -> str | None:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name.strip())
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    s = re.sub(r"_+", "_", s)
    for prefix in RESERVED_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
    if s and not s[0].isalpha():
        s = "event_" + s
    s = s[:40].rstrip("_")
    return s if s and _ga4_name_problem(s) is None else None


# --- rules ------------------------------------------------------------------

def rule_broken_trigger_refs(c: Container) -> list[Finding]:
    out = []
    for t in c.tags:
        missing = [i for i in (t.get("firingTriggerId", []) or []) + (t.get("blockingTriggerId", []) or [])
                   if i not in c.trigger_by_id and c.trigger_name(i).startswith("missing")]
        if missing:
            out.append(_f("BROKEN_TRIGGER_REF", "high", f"Tag '{t['name']}' references missing triggers",
                          f"Trigger IDs {', '.join(missing)} do not exist in the container.",
                          "Re-attach the intended trigger or remove the reference.", "tag", [t]))
    return out


def rule_broken_variable_refs(c: Container) -> list[Finding]:
    known = set(c.var_by_name) | c.builtin_names
    out = []
    for kind, items in (("tag", c.tags), ("trigger", c.triggers), ("variable", c.variables)):
        for e in items:
            missing = sorted(r for r in var_refs(e) if r not in known and r != "_event")
            if missing:
                out.append(_f("BROKEN_VARIABLE_REF", "high", f"{kind.title()} '{e['name']}' uses undefined variables",
                              f"References {', '.join('{{' + m + '}}' for m in missing)}, which are neither user-defined nor enabled built-in variables. They resolve to undefined.",
                              "Enable the built-in variable or create the missing variable; fix typos in the name.", kind, [e]))
    return out


def rule_tag_without_trigger(c: Container) -> list[Finding]:
    sequenced = set()
    for t in c.tags:
        for key in ("setupTag", "teardownTag"):
            for s in t.get(key, []) or []:
                sequenced.add(s.get("tagName"))
    out = []
    for t in c.tags:
        if not t.get("paused") and not t.get("firingTriggerId") and t["name"] not in sequenced:
            out.append(_f("TAG_NO_TRIGGER", "medium", f"Tag '{t['name']}' has no firing trigger",
                          "The tag never fires, which usually means it was left behind or its trigger was deleted.",
                          "Attach the correct trigger, or pause/delete the tag.", "tag", [t], fixable=True))
    return out


def rule_paused_tags(c: Container) -> list[Finding]:
    return [_f("PAUSED_TAG", "low", f"Tag '{t['name']}' is paused", "Paused tags add clutter and can be re-enabled by mistake.",
               "Delete it if it is no longer needed (it stays in version history).", "tag", [t], fixable=True)
            for t in c.tags if t.get("paused")]


def rule_unused_triggers(c: Container) -> list[Finding]:
    used = c.referenced_trigger_ids()
    return [_f("UNUSED_TRIGGER", "low", f"Trigger '{t['name']}' is not used", "No tag fires or is blocked by this trigger.",
               "Delete it.", "trigger", [t], fixable=True)
            for t in c.triggers if t["triggerId"] not in used]


def rule_unused_variables(c: Container) -> list[Finding]:
    out = []
    for v in c.variables:
        used = False
        for items in (c.tags, c.triggers, c.variables):
            for e in items:
                if e is v:
                    continue
                if v["name"] in var_refs(e):
                    used = True
                    break
            if used:
                break
        if not used:
            out.append(_f("UNUSED_VARIABLE", "low", f"Variable '{v['name']}' is not used",
                          "No tag, trigger or other variable references it.", "Delete it.", "variable", [v], fixable=True))
    return out


def rule_duplicate_tags(c: Container) -> list[Finding]:
    groups = defaultdict(list)
    for t in c.tags:
        if not t.get("paused"):
            groups[signature(t)].append(t)
    out = []
    for tags in groups.values():
        if len(tags) > 1:
            tags = sorted(tags, key=lambda t: int(t["tagId"]))
            out.append(_f("DUPLICATE_TAG", "high", f"{len(tags)} identical tags: {', '.join(t['name'] for t in tags)}",
                          "These tags have the same type, settings and triggers, so each event is sent more than once (inflated counts).",
                          f"Keep '{tags[0]['name']}' and delete the others.", "tag", tags, fixable=True))
    return out


def rule_duplicate_google_tags(c: Container) -> list[Finding]:
    by_id = defaultdict(list)
    for t in c.tags:
        if t["type"] == "googtag" and not t.get("paused"):
            tid = param(t, "tagId")
            if tid:
                by_id[tid].append(t)
    out = []
    for tid, tags in by_id.items():
        if len(tags) < 2:
            continue
        trig_sets = [set(t.get("firingTriggerId", []) or []) for t in tags]
        overlap = set.intersection(*trig_sets) if trig_sets else set()
        if overlap or all(ALL_PAGES in s for s in trig_sets):
            out.append(_f("DUPLICATE_GOOGLE_TAG", "high", f"Google tag {tid} loads {len(tags)} times",
                          f"Tags {', '.join(t['name'] for t in tags)} all configure {tid} on the same trigger, which can double-count page views.",
                          "Keep one Google tag per measurement ID; move per-page settings into it or into event tags.", "tag", tags,
                          suffix=tid))
    return out


def rule_ga4_event_names(c: Container) -> list[Finding]:
    out = []
    for t in c.tags:
        if t["type"] != "gaawe":
            continue
        name = _event_name(t)
        if not name:
            out.append(_f("GA4_EVENT_NO_NAME", "high", f"GA4 event tag '{t['name']}' has no event name", "GA4 drops events with no name.",
                          "Set an event name.", "tag", [t]))
            continue
        problem = _ga4_name_problem(name)
        if problem:
            fix = sanitize_event_name(name)
            out.append(_f("GA4_EVENT_NAME_INVALID", "high", f"Event name '{name}' {problem}",
                          f"Tag '{t['name']}' sends '{name}'. GA4 rejects or ignores it.",
                          f"Rename the event{f' to {fix!r}' if fix else ''}. Renaming starts a new series in reports.",
                          "tag", [t], fixable=bool(fix)))
        elif "{{" not in name and name != name.lower():
            out.append(_f("GA4_EVENT_NAME_STYLE", "low", f"Event name '{name}' is not snake_case",
                          "GA4 event names are case-sensitive; mixed case leads to split reports (e.g. Sign_Up vs sign_up).",
                          "Use lowercase snake_case for new events. Rename only if continuity is not needed.", "tag", [t]))
    return out


def rule_ga4_event_params(c: Container) -> list[Finding]:
    out = []
    for t in c.tags:
        if t["type"] != "gaawe":
            continue
        params = _event_params(t)
        bad = [p for p in params if "{{" not in p and _ga4_name_problem(p)]
        if len(params) > 25:
            out.append(_f("GA4_TOO_MANY_PARAMS", "high", f"Tag '{t['name']}' sends {len(params)} parameters",
                          "GA4 accepts at most 25 event parameters per event; the rest are dropped.",
                          "Remove parameters you don't report on.", "tag", [t]))
        if bad:
            out.append(_f("GA4_PARAM_NAME_INVALID", "high", f"Tag '{t['name']}' has invalid parameter names",
                          f"Invalid: {', '.join(bad)}. Parameter names must start with a letter, use letters/numbers/underscores, max 40 characters, no reserved prefixes.",
                          "Rename the parameters (and any custom dimensions that use them).", "tag", [t]))
    return out


def ga4_measurement_ids(c: Container) -> set[str]:
    ids = set()
    for t in c.tags:
        if t["type"] == "googtag" and isinstance(param(t, "tagId"), str) and param(t, "tagId").startswith("G-"):
            ids.add(param(t, "tagId"))
    return ids


def rule_ga4_missing_measurement_id(c: Container) -> list[Finding]:
    ids = ga4_measurement_ids(c)
    out = []
    for t in c.tags:
        if t["type"] == "gaawe" and not param(t, "measurementIdOverride") and not param(t, "measurementId"):
            out.append(_f("GA4_NO_MEASUREMENT_ID", "critical", f"GA4 event tag '{t['name']}' has no measurement ID",
                          "The tag has nowhere to send data.",
                          f"Set the measurement ID{' to ' + next(iter(ids)) if len(ids) == 1 else ''}.", "tag", [t],
                          fixable=len(ids) == 1))
    return out


def rule_hardcoded_measurement_id(c: Container) -> list[Finding]:
    uses = defaultdict(list)
    for t in c.tags:
        for key in ("tagId", "measurementIdOverride"):
            v = param(t, key)
            if isinstance(v, str) and MEASUREMENT_ID.match(v):
                uses[v].append(t)
    return [_f("HARDCODED_MEASUREMENT_ID", "low", f"Measurement ID {mid} is typed into {len(tags)} tags",
               "Changing properties or adding a test property means editing every tag.",
               "Store the ID in a Constant variable and reference it.", "tag", tags, fixable=True, suffix=mid)
            for mid, tags in uses.items() if len(tags) >= 2]


def rule_legacy_tags(c: Container) -> list[Finding]:
    out = []
    for t in c.tags:
        if t["type"] == "ua" and not t.get("paused"):
            out.append(_f("LEGACY_UA_TAG", "high", f"Universal Analytics tag '{t['name']}' is still active",
                          "Universal Analytics stopped processing data in 2024; the tag only adds page weight.",
                          "Pause it now, delete it once you've confirmed nothing depends on it.", "tag", [t], fixable=True))
        elif t["type"] == "gaawc":
            out.append(_f("LEGACY_GA4_CONFIG", "info", f"Tag '{t['name']}' uses the old GA4 Configuration tag type",
                          "Google migrated these to the Google tag; behaviour is the same.", "No action needed; new setups use the Google tag.", "tag", [t]))
    return out


def rule_custom_html(c: Container) -> list[Finding]:
    out = []
    for t in c.tags:
        if t["type"] != "html":
            continue
        html = param(t, "html") or ""
        issues = []
        if "document.write" in html:
            issues.append("uses document.write (blocks rendering, ignored on async pages)")
        if re.search(r"src=[\"']http://", html):
            issues.append("loads a script over plain http (blocked as mixed content)")
        if re.search(r"\beval\s*\(", html):
            issues.append("calls eval()")
        if len(html) > 20000:
            issues.append(f"is very large ({len(html)} characters)")
        if issues:
            out.append(_f("CUSTOM_HTML_RISK", "medium", f"Custom HTML tag '{t['name']}' needs review", "; ".join(issues).capitalize() + ".",
                          "Replace with a template from the Community Template Gallery where possible, or fix the code.", "tag", [t]))
        elif ALL_PAGES in (t.get("firingTriggerId") or []) and not t.get("paused"):
            out.append(_f("CUSTOM_HTML_ALL_PAGES", "low", f"Custom HTML tag '{t['name']}' runs on every page",
                          "Custom HTML on every page adds script execution to all page loads and bypasses template permissions.",
                          "Use a gallery template if one exists, or narrow the trigger.", "tag", [t]))
    return out


def rule_consent(c: Container) -> list[Finding]:
    out = []
    for t in c.tags:
        if t["type"] in GOOGLE_TAG_TYPES or t.get("paused"):
            continue  # Google tags have built-in consent checks
        status = (t.get("consentSettings") or {}).get("consentStatus", "notSet")
        if status == "notSet":
            out.append(_f("CONSENT_NOT_SET", "medium", f"Tag '{t['name']}' has no consent requirement",
                          "This non-Google tag will fire regardless of the visitor's consent choice.",
                          "If it sets cookies or sends data to a third party, require ad_storage / analytics_storage consent "
                          "(Advanced Settings > Consent Settings).", "tag", [t]))
    return out


def rule_conversion_linker(c: Container) -> list[Finding]:
    has_ads = any(t["type"] in {"awct", "flc", "fls"} and not t.get("paused") for t in c.tags)
    has_linker = any(t["type"] == "gclidw" and not t.get("paused") for t in c.tags)
    has_googtag = any(t["type"] == "googtag" and isinstance(param(t, "tagId"), str) and param(t, "tagId").startswith("AW-") for t in c.tags)
    if has_ads and not has_linker and not has_googtag:
        return [_f("MISSING_CONVERSION_LINKER", "medium", "Google Ads/Floodlight tags without a Conversion Linker",
                   "Without a Conversion Linker, click IDs are not stored in first-party cookies and conversions are under-reported, especially in Safari.",
                   "Add a Conversion Linker tag firing on All Pages.", fixable=True, suffix="container")]
    return []


def rule_duplicate_triggers(c: Container) -> list[Finding]:
    groups = defaultdict(list)
    for t in c.triggers:
        groups[signature(t)].append(t)
    out = []
    for trigs in groups.values():
        if len(trigs) > 1:
            trigs = sorted(trigs, key=lambda t: int(t["triggerId"]))
            out.append(_f("DUPLICATE_TRIGGER", "low", f"{len(trigs)} identical triggers: {', '.join(t['name'] for t in trigs)}",
                          "Identical triggers make the container harder to maintain.",
                          f"Point all tags at '{trigs[0]['name']}' and delete the copies.", "trigger", trigs, fixable=True))
    return out


def rule_catch_all_custom_event(c: Container) -> list[Finding]:
    out = []
    for t in c.triggers:
        if t["type"] != "customEvent":
            continue
        for cond in t.get("customEventFilter", []) or []:
            arg1 = next((p.get("value") for p in cond.get("parameter", []) if p.get("key") == "arg1"), None)
            if cond.get("type") == "matchRegex" and arg1 in {".*", ".+", "^.*$"}:
                users = [tag["name"] for tag in c.tags if t["triggerId"] in (tag.get("firingTriggerId") or [])]
                out.append(_f("CATCH_ALL_TRIGGER", "medium", f"Trigger '{t['name']}' fires on every dataLayer event",
                              f"It matches all events (including gtm.js, gtm.dom, gtm.load). Tags using it: {', '.join(users) or 'none'}.",
                              "Match the specific event names you need.", "trigger", [t]))
    return out


def rule_ecommerce_data(c: Container) -> list[Finding]:
    out = []
    for t in c.tags:
        if t["type"] != "gaawe":
            continue
        name = _event_name(t)
        if name in ECOMMERCE_EVENTS:
            send = str(param(t, "sendEcommerceData", "false")).lower() == "true"
            if not send and "items" not in _event_params(t):
                out.append(_f("ECOMMERCE_NO_ITEMS", "high", f"'{name}' tag '{t['name']}' sends no ecommerce data",
                              "Neither 'Send Ecommerce data' nor an items parameter is set, so revenue/item reports stay empty.",
                              "Enable 'Send Ecommerce data' from the dataLayer (after checking the dataLayer has an ecommerce object).",
                              "tag", [t], fixable=True))
    return out


def rule_naming(c: Container, pattern: str) -> list[Finding]:
    if not pattern:
        return []
    rx = re.compile(pattern)
    bad = [t for t in c.tags if not rx.match(t["name"])]
    if not bad:
        return []
    return [_f("NAMING_CONVENTION", "low", f"{len(bad)} of {len(c.tags)} tags don't follow the naming convention",
               "Names: " + ", ".join(t["name"] for t in bad[:30]) + (" ..." if len(bad) > 30 else ""),
               "Rename to 'Platform - Type - Detail', e.g. 'GA4 - Event - sign_up'.", "tag", bad, suffix="tags")]


def rule_folders(c: Container) -> list[Finding]:
    total = len(c.tags) + len(c.triggers) + len(c.variables)
    loose = [e for e in c.tags + c.triggers + c.variables if not e.get("parentFolderId")]
    if total >= 20 and len(loose) / total > 0.5:
        return [Finding(id="NO_FOLDERS:container", rule="NO_FOLDERS", severity="info",
                        title=f"{len(loose)} of {total} items are not in folders",
                        detail="Folders make large containers much easier to review.",
                        recommendation="Group items into folders by platform or feature.")]
    return []


RULES = [
    rule_ga4_missing_measurement_id, rule_broken_trigger_refs, rule_broken_variable_refs, rule_duplicate_tags,
    rule_duplicate_google_tags, rule_ga4_event_names, rule_ga4_event_params, rule_ecommerce_data, rule_legacy_tags,
    rule_tag_without_trigger, rule_custom_html, rule_consent, rule_conversion_linker, rule_catch_all_custom_event,
    rule_duplicate_triggers, rule_hardcoded_measurement_id, rule_paused_tags, rule_unused_triggers,
    rule_unused_variables, rule_folders,
]


def run_audit(c: Container, naming_pattern: str = "") -> dict:
    findings: list[Finding] = []
    for rule in RULES:
        findings.extend(rule(c))
    findings.extend(rule_naming(c, naming_pattern))
    findings.sort(key=lambda f: SEVERITY_ORDER.index(f.severity))
    score = max(0, 100 - sum(SEVERITY_WEIGHT[f.severity] for f in findings))
    counts = {s: sum(1 for f in findings if f.severity == s) for s in SEVERITY_ORDER}
    return {
        "container": c.name,
        "public_id": c.container.get("publicId"),
        "version_id": c.version_id,
        "score": score,
        "counts": counts,
        "totals": {"tags": len(c.tags), "triggers": len(c.triggers), "variables": len(c.variables)},
        "findings": [f.to_dict() for f in findings],
    }
