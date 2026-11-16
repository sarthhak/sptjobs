"""Deeper GTM checks: consent enforcement, GA4 configuration mistakes, risky custom code and trigger logic."""

from __future__ import annotations

import re

from .consent import (CMP_SCRIPT, consent_matrix, consent_mode_status, consent_variables, template_names, third_party_live,
                      trigger_event, vendor)
from .container import Container, map_value, param, var_refs
from .rules import Finding, _f

GA4_TAGS = ("gaawe", "googtag", "gaawc")
# Outcome events (a lead, sign-up, order...), not clicks that merely show intent.
CONVERSION_EVENT = re.compile(r"^(generate_lead|sign_up|signup|purchase|form_submit|submit_form|form_success|lead|conversion|"
                              r"close_convert_lead|qualify_lead|working_lead)$|_lead$|_success$|_complete(d)?$|_submit(ted)?$", re.I)


def _live(c: Container, types=None):
    return [t for t in c.tags if not t.get("paused") and (types is None or t["type"] in types)]


def _settings_rows(tag: dict) -> list[tuple[str, str]]:
    """(name, value) pairs from GA4 parameter / config / fields tables."""
    rows = []
    for key, name_key, value_key in (("eventSettingsTable", "parameter", "parameterValue"),
                                     ("configSettingsTable", "parameter", "parameterValue"),
                                     ("eventParameters", "name", "value"), ("fieldsToSet", "fieldName", "value")):
        for r in param(tag, key, []) or []:
            n = map_value(r, name_key)
            if n is not None:
                rows.append((str(n), str(map_value(r, value_key))))
    return rows


def _conditions(t: dict) -> list[dict]:
    return (t.get("filter") or []) + (t.get("autoEventFilter") or []) + (t.get("customEventFilter") or [])


def _arg(cond: dict, key: str):
    return next((p.get("value") for p in cond.get("parameter", []) if p.get("key") == key), None)


def _live_trigger_users(c: Container) -> dict[str, list[str]]:
    users: dict[str, list[str]] = {}
    for t in _live(c):
        for i in (t.get("firingTriggerId") or []) + (t.get("blockingTriggerId") or []):
            users.setdefault(i, []).append(t["name"])
    return users


# --- GA4 configuration ---------------------------------------------------------

def rule_dynamic_param_names(c: Container) -> list[Finding]:
    out = []
    for t in _live(c, GA4_TAGS):
        dyn = [f"{n} = {v}" for n, v in _settings_rows(t) if "{{" in n]
        if dyn:
            out.append(_f("DYNAMIC_PARAM_NAMES", "high", f"Tag '{t['name']}' uses variables as parameter names",
                          f"Parameter rows: {'; '.join(dyn)}.", "Use fixed keys, e.g. link_text = {{Click Text}}, link_url = {{Click URL}}.",
                          "tag", [t]))
    return out


def rule_debug_mode(c: Container) -> list[Finding]:
    out = []
    for t in _live(c, GA4_TAGS):
        rows = [f"{n} = {v}" for n, v in _settings_rows(t) if n == "debug_mode"]
        if rows:
            out.append(_f("DEBUG_MODE_SET", "high", f"Tag '{t['name']}' sets debug_mode", f"Setting: {'; '.join(rows)}.",
                          "Remove the debug_mode row; GTM Preview already enables DebugView.", "tag", [t]))
    return out


def rule_anonymize_ip(c: Container) -> list[Finding]:
    out = []
    for t in _live(c, GA4_TAGS):
        if any(n == "anonymize_ip" for n, _ in _settings_rows(t)):
            out.append(_f("ANONYMIZE_IP", "low", f"Tag '{t['name']}' sets anonymize_ip", "GA4 ignores this setting.",
                          "Remove the anonymize_ip row.", "tag", [t]))
    return out


def rule_google_tag_setup_only(c: Container) -> list[Finding]:
    setup_names = {s.get("tagName") for t in _live(c) for s in t.get("setupTag") or []}
    return [_f("GOOGLE_TAG_SETUP_ONLY", "medium", f"Google tag '{t['name']}' only loads as a setup tag",
               "It has no firing trigger of its own.", "Fire the Google tag on Initialization - All Pages (with your consent conditions).",
               "tag", [t])
            for t in _live(c, ("googtag", "gaawc")) if not t.get("firingTriggerId") and t["name"] in setup_names]


def rule_mixed_measurement_routing(c: Container) -> list[Finding]:
    tags = _live(c, ("gaawe",))
    mids = {t["tagId"]: str(param(t, "measurementIdOverride") or "") for t in tags}
    via_var = [t for t in tags if "{{" in mids[t["tagId"]]]
    literal = [t for t in tags if mids[t["tagId"]].startswith("G-")]
    if len(via_var) >= 2 and literal:
        var = sorted({mids[t["tagId"]] for t in via_var})
        return [_f("MIXED_MEASUREMENT_ROUTING", "high", f"{len(literal)} GA4 tags hard-code a property while {len(via_var)} route via {', '.join(var)}",
                   "These tags always send to the hard-coded property.", f"Use {var[0]} as the measurement ID on these tags too.",
                   "tag", literal, suffix="routing")]
    return []


def rule_lookup_no_default(c: Container) -> list[Finding]:
    mid_refs = set()
    for t in _live(c, GA4_TAGS):
        for key in ("measurementIdOverride", "tagId"):
            mid_refs |= var_refs(param(t, key) or "")
    out = []
    for v in c.variables:
        if v["type"] in ("smm", "remm") and str(param(v, "setDefaultValue", "false")).lower() != "true":
            feeds_mid = v["name"] in mid_refs
            out.append(_f("LOOKUP_NO_DEFAULT", "high" if feeds_mid else "low", f"{'Lookup' if v['type'] == 'smm' else 'RegEx'} table '{v['name']}' has no default value",
                          "Unmapped inputs return undefined" + (" - and it supplies the GA4 measurement ID, so those hits are lost." if feeds_mid else "."),
                          "Set a default value (e.g. the main property, or a 'catch-all' value you can spot in reports).", "variable", [v]))
    return out


def rule_no_conversion_event(c: Container) -> list[Finding]:
    tags = _live(c, ("gaawe",))
    if tags and not any(CONVERSION_EVENT.search(str(param(t, "eventName") or "")) for t in tags):
        return [_f("NO_CONVERSION_EVENT", "medium", "No live GA4 tag sends a conversion-type event",
                   f"{len(tags)} live GA4 event tags send engagement events only.",
                   "Add a dataLayer event on confirmed form success / purchase (e.g. generate_lead) and mark it as a key event.",
                   suffix="container")]
    return []


# --- custom code ------------------------------------------------------------------

def rule_custom_code(c: Container) -> list[Finding]:
    out = []
    for t in _live(c, ("html",)):
        html = param(t, "html") or ""
        if re.search(r"location\.reload\s*\(|location\.href\s*=\s*(window\.)?location\.href", html):
            out.append(_f("PAGE_RELOAD", "high", f"Tag '{t['name']}' reloads the page", "The code calls location.reload().",
                          "Remove the reload; let consent updates reach tags without reloading (Consent Mode / consent events).", "tag", [t]))
        intervals = [int(n) for n in re.findall(r"setInterval\([\s\S]{0,400}?,\s*(\d+)\s*\)", html)]
        if any(n < 100 for n in intervals):
            out.append(_f("SHORT_POLLING", "medium", f"Tag '{t['name']}' polls every {min(intervals)} ms", "setInterval with a very short delay.",
                          "Use an event (or at least 500 ms) and clear the interval when done.", "tag", [t]))
        if re.search(r"\b(?:self|window)\.datalayer\b|(?<![\w.])datalayer\.push", html):
            out.append(_f("DATALAYER_TYPO", "medium", f"Tag '{t['name']}' pushes to 'datalayer' (lower-case l)",
                          "GTM only reads window.dataLayer.", "Change to window.dataLayer.push(...).", "tag", [t]))
        if re.search(r"document\.cookie\.split", html) and re.search(r"1970|max-age=0|expires=", html, re.I):
            out.append(_f("COOKIE_WIPE", "medium", f"Tag '{t['name']}' deletes cookies in bulk", "The code loops over document.cookie and expires them.",
                          "Replace with a deny-list of specific advertising/analytics cookies per consent category.", "tag", [t]))
        if CMP_SCRIPT.search(html):
            out.append(_f("CMP_VIA_GTM", "medium", f"Consent banner script is loaded by tag '{t['name']}'", "The CMP loads after GTM.",
                          "Load the CMP script in the page <head> before the GTM snippet.", "tag", [t]))
    return out


# --- triggers -----------------------------------------------------------------------

def rule_trigger_logic(c: Container) -> list[Finding]:
    users = _live_trigger_users(c)
    out = []
    for t in c.triggers:
        if t["triggerId"] not in users:
            continue
        for cond in _conditions(t):
            arg1 = str(_arg(cond, "arg1") or "")
            if cond.get("type") == "matchRegex" and (arg1.startswith("|") or arg1.endswith("|") or "||" in arg1):
                out.append(_f("REGEX_MATCHES_ALL", "medium", f"Trigger '{t['name']}' has a regex that matches everything",
                              f"{_arg(cond, 'arg0')} matches regex '{arg1}' - the empty alternative matches any value.",
                              "Remove the stray '|' and anchor the pattern.", "trigger", [t]))
                break
        if t["type"] in ("pageview", "domReady", "windowLoaded"):
            click_vars = sorted({r for cond in _conditions(t) for r in var_refs(_arg(cond, "arg0") or "") if r.startswith(("Click ", "Form "))})
            if click_vars:
                out.append(_f("CLICK_VARS_ON_PAGE_LOAD", "high", f"Page-load trigger '{t['name']}' tests {', '.join(click_vars)}",
                              f"Click/form variables are empty at page load. Used by: {', '.join(users[t['triggerId']][:5])}.",
                              "Use the variable the condition was meant to test (e.g. the consent cookie), or delete the condition.", "trigger", [t]))
    return out


def rule_triggers_only_paused(c: Container) -> list[Finding]:
    live = _live_trigger_users(c)
    paused_users: dict[str, list[str]] = {}
    for t in c.tags:
        if t.get("paused"):
            for i in (t.get("firingTriggerId") or []) + (t.get("blockingTriggerId") or []):
                paused_users.setdefault(i, []).append(t["name"])
    trigs = [t for t in c.triggers if t["triggerId"] in paused_users and t["triggerId"] not in live]
    if not trigs:
        return []
    return [_f("TRIGGERS_ONLY_PAUSED", "low", f"{len(trigs)} triggers are used only by paused tags",
               "These triggers only serve paused tags.", "Delete them together with the paused tags.", "trigger", trigs, suffix="paused")]


# --- consent ------------------------------------------------------------------------

def rule_consent(c: Container) -> list[Finding]:
    rows = consent_matrix(c)
    out = []
    by_tag: dict[str, list[dict]] = {}
    for r in rows:
        by_tag.setdefault(r["tag_id"], []).append(r)
    for tag_id, trs in by_tag.items():
        tag = c.tag_by_id[tag_id]
        unchecked = [r for r in trs if r["risk"] == "No consent check"]
        if unchecked:
            ad = trs[0]["advertising"]
            who = trs[0]["vendor"] or "Third-party"
            out.append(_f("CONSENT_NOT_ENFORCED", "high" if ad else "medium",
                          f"{who} tag '{tag['name']}' fires without a consent check",
                          f"Fires on {', '.join(r['firing_trigger'] for r in unchecked)} with no consent setting, consent condition or working exception.",
                          "Fire it on a consent-gated trigger, or set Consent Settings > Require additional consent (ad_storage).", "tag", [tag]))
        mismatched = [r for r in trs if r["exception_status"].startswith("Ineffective") and not r["self_gated"]]
        if mismatched:
            out.append(_f("EXCEPTION_EVENT_MISMATCH", "high", f"Exceptions on '{tag['name']}' never apply",
                          "; ".join(f"fires on '{r['firing_trigger']}' ({r['firing_event']}) but its exceptions listen to {r['exception_events']}"
                                    for r in mismatched) + ".",
                          "Use exceptions of the same event type as each firing trigger, or put the consent condition on the firing trigger itself.",
                          "tag", [tag]))
    return out


def rule_consent_mode(c: Container) -> list[Finding]:
    status = consent_mode_status(c)
    templates = template_names(c)
    ad_tags = [t for t in _live(c) if (v := vendor(c, t, templates)) and v[1]]
    if status["live"] or not ad_tags:
        return []
    note = f" A consent template exists but is paused: {', '.join(status['paused'])}." if status["paused"] else ""
    return [_f("CONSENT_MODE_MISSING", "high", "Google Consent Mode v2 is not implemented",
               "No live tag sets consent defaults/updates." + note,
               "Configure your CMP's Consent Mode template (or gtag consent default/update) on Consent Initialization - All Pages.",
               suffix="container")]


EXTRA_RULES = [rule_dynamic_param_names, rule_debug_mode, rule_anonymize_ip, rule_google_tag_setup_only, rule_mixed_measurement_routing,
               rule_lookup_no_default, rule_no_conversion_event, rule_custom_code, rule_trigger_logic, rule_triggers_only_paused,
               rule_consent, rule_consent_mode]

__all__ = ["EXTRA_RULES", "template_names", "vendor", "trigger_event", "consent_variables"]
