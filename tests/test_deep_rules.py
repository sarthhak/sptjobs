"""The deeper checks, on a container that reproduces problems found in a real manual audit."""

from app.audit.consent import consent_matrix
from app.audit.container import Container
from app.audit.rules import run_audit

ALL_PAGES = "2147479553"


def p(key, value, ptype="template"):
    return {"type": ptype, "key": key, "value": value}


def cond(kind, arg0, arg1, negate=False):
    params = [p("arg0", arg0), p("arg1", arg1)] + ([p("negate", "true", "boolean")] if negate else [])
    return {"type": kind, "parameter": params}


def row(name, value):
    return {"type": "map", "map": [p("parameter", name), p("parameterValue", value)]}


def version():
    return {
        "containerVersionId": "38", "container": {"name": "MF", "publicId": "GTM-X"},
        "builtInVariable": [{"name": n} for n in ("Page URL", "Click Text", "Click Classes", "Click URL", "Event", "Debug Mode")],
        "variable": [
            {"variableId": "3", "name": "GDPR Cookie Privacy", "type": "k", "parameter": [p("name", "cmapi_cookie_privacy")]},
            {"variableId": "23", "name": "Country lookup", "type": "smm", "parameter": [p("input", "{{Page URL}}"), p("setDefaultValue", "false", "boolean")]},
        ],
        "trigger": [
            {"triggerId": "58", "name": "Ad block - link clicks", "type": "linkClick",
             "filter": [cond("doesNotContain", "{{GDPR Cookie Privacy}}", "3")]},
            {"triggerId": "94", "name": "Pageviews - permit 1,2,3", "type": "pageview",
             "filter": [cond("contains", "{{GDPR Cookie Privacy}}", "permit 1,2,3")]},
            {"triggerId": "19", "name": "Level 4 Preference Blocking Trigger", "type": "pageview",
             "filter": [cond("contains", "{{Click Classes}}", "permit")]},
            {"triggerId": "4", "name": "Request a Demo click", "type": "click",
             "filter": [cond("matchRegex", "{{Click Classes}}", "btn |")]},
        ],
        "tag": [
            {"tagId": "182", "name": "StackAdapt Pixel", "type": "html", "firingTriggerId": [ALL_PAGES], "blockingTriggerId": ["58"],
             "parameter": [p("html", "<script>!function(s,a,e,v,n,t,z){saq('ts','abc')}</script><script src='https://tags.srv.stackadapt.com/events.js'></script>")]},
            {"tagId": "117", "name": "MF Facebook Pixel", "type": "html", "firingTriggerId": ["94"],
             "parameter": [p("html", "<script>fbq('init','1');fbq('track','PageView');</script>")]},
            {"tagId": "130", "name": "G4 - Analytics", "type": "googtag", "parameter": [
                p("tagId", "{{Country lookup}}"),
                {"type": "list", "key": "configSettingsTable", "list": [row("debug_mode", "{{Debug Mode}}"), row("anonymize_ip", "true")]}]},
            {"tagId": "26", "name": "GA4 - Pageview", "type": "gaawe", "firingTriggerId": ["94"], "blockingTriggerId": ["19"],
             "setupTag": [{"tagName": "G4 - Analytics"}],
             "parameter": [p("eventName", "page_view"), p("measurementIdOverride", "{{Country lookup}}")]},
            {"tagId": "63", "name": "GA4 - Build & Quote", "type": "gaawe", "firingTriggerId": ["4"],
             "parameter": [p("eventName", "build_and_quote_click"), p("measurementIdOverride", "{{Country lookup}}")]},
            {"tagId": "171", "name": "GA4 Event Book a Service Click", "type": "gaawe", "firingTriggerId": ["4"],
             "parameter": [p("eventName", "book_a_service_click"), p("measurementIdOverride", "G-2V4ZZ4K183"),
                           {"type": "list", "key": "eventSettingsTable", "list": [row("{{Click Text}}", "{{Click Text}}")]}]},
            {"tagId": "70", "name": "truste banner opt out", "type": "html", "firingTriggerId": [ALL_PAGES],
             "parameter": [p("html", "<script>window.location.reload();</script>")]},
            {"tagId": "103", "name": "truste overlay consent", "type": "html", "firingTriggerId": [ALL_PAGES],
             "parameter": [p("html", "<script>setInterval(function(){check()}, 10)</script>")]},
            {"tagId": "143", "name": "TrustArc Event Generation", "type": "html", "firingTriggerId": [ALL_PAGES],
             "parameter": [p("html", "<script>self.datalayer && self.datalayer.push({event:'Consent Changed'})</script>")]},
            {"tagId": "148", "name": "All Cookie Removal", "type": "html", "firingTriggerId": [ALL_PAGES],
             "parameter": [p("html", "<script>document.cookie.split(';').forEach(function(c){document.cookie=c+'=;expires=Thu, 01 Jan 1970'})</script>")]},
        ],
    }


def by_rule(result):
    return {f["rule"]: f for f in result["findings"]}


def test_findings_from_manual_audit_are_detected():
    f = by_rule(run_audit(Container(version()), ""))
    # StackAdapt fires on All Pages (gtm.js) but its only exception listens to link clicks.
    assert f["EXCEPTION_EVENT_MISMATCH"]["entity_ids"] == ["182"]
    assert f["CONSENT_NOT_ENFORCED"]["entity_ids"] == ["182"] and f["CONSENT_NOT_ENFORCED"]["severity"] == "high"
    # The Facebook pixel is gated by a consent condition on its own trigger, so it's fine.
    assert "117" not in f["CONSENT_NOT_ENFORCED"]["entity_ids"]
    assert f["CONSENT_MODE_MISSING"]["severity"] == "high"
    assert f["DEBUG_MODE_SET"]["entity_ids"] == ["130"] and f["ANONYMIZE_IP"]["entity_ids"] == ["130"]
    assert f["DYNAMIC_PARAM_NAMES"]["entity_ids"] == ["171"]
    assert f["MIXED_MEASUREMENT_ROUTING"]["entity_ids"] == ["171"]
    assert f["LOOKUP_NO_DEFAULT"]["entity_ids"] == ["23"] and f["LOOKUP_NO_DEFAULT"]["severity"] == "high"
    assert f["GOOGLE_TAG_SETUP_ONLY"]["entity_ids"] == ["130"]
    assert f["CLICK_VARS_ON_PAGE_LOAD"]["entity_ids"] == ["19"]
    assert f["REGEX_MATCHES_ALL"]["entity_ids"] == ["4"]
    assert f["PAGE_RELOAD"]["entity_ids"] == ["70"] and f["SHORT_POLLING"]["entity_ids"] == ["103"]
    assert f["DATALAYER_TYPO"]["entity_ids"] == ["143"] and f["COOKIE_WIPE"]["entity_ids"] == ["148"]
    assert f["NO_CONVERSION_EVENT"]["severity"] == "medium"


def test_consent_matrix():
    rows = {(r["tag_id"], r["firing_trigger_id"]): r for r in consent_matrix(Container(version()))}
    sa = rows[("182", ALL_PAGES)]
    assert sa["firing_event"] == "gtm.js" and sa["exception_events"] == "gtm.linkClick"
    assert sa["exception_status"] == "Ineffective (event mismatch)" and sa["risk"] == "No consent check" and sa["vendor"] == "StackAdapt"
    fb = rows[("117", "94")]
    assert fb["self_gated"] and fb["risk"] == "OK" and fb["vendor"] == "Meta Pixel"
    assert rows[("148", ALL_PAGES)]["risk"] == "Consent tool (review)"
    assert rows[("26", "94")]["risk"] == "Google built-in consent checks"
