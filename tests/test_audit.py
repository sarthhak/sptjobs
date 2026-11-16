from app.audit.container import Container
from app.audit.rules import run_audit, sanitize_event_name

NAMING = r"^[^-]+ - [^-]+ - .+$"


def rules_by_entity(result):
    return {(f["rule"], tuple(f["entity_ids"])) for f in result["findings"]}


def test_messy_container_findings(version):
    result = run_audit(Container(version), NAMING)
    found = rules_by_entity(result)
    expected = {
        ("GA4_NO_MEASUREMENT_ID", ("5",)),
        ("BROKEN_TRIGGER_REF", ("12",)),
        ("BROKEN_VARIABLE_REF", ("3",)),
        ("DUPLICATE_TAG", ("2", "3")),
        ("GA4_EVENT_NAME_INVALID", ("4",)),
        ("ECOMMERCE_NO_ITEMS", ("2", "3")),
        ("LEGACY_UA_TAG", ("6",)),
        ("TAG_NO_TRIGGER", ("11",)),
        ("CUSTOM_HTML_RISK", ("8",)),
        ("CUSTOM_HTML_ALL_PAGES", ("9",)),
        ("CONSENT_NOT_ENFORCED", ("8",)),
        ("CONSENT_MODE_MISSING", ()),
        ("MISSING_CONVERSION_LINKER", ()),
        ("CATCH_ALL_TRIGGER", ("13",)),
        ("DUPLICATE_TRIGGER", ("10", "11")),
        ("LOOKUP_NO_DEFAULT", ("3",)),
        ("PAUSED_TAG", ("7",)),
        ("UNUSED_TRIGGER", ("12",)),
        ("UNUSED_VARIABLE", ("2",)),
        ("NAMING_CONVENTION", ("4",)),
    }
    assert expected <= found, expected - found
    hardcoded = [f for f in result["findings"] if f["rule"] == "HARDCODED_MEASUREMENT_ID"]
    assert hardcoded and set(hardcoded[0]["entity_ids"]) == {"1", "2", "3", "4", "11", "12"}
    # Variable 3 is used by tag 9 and variable 1 by tags 2/3, so they are not "unused".
    assert ("UNUSED_VARIABLE", ("1",)) not in found and ("UNUSED_VARIABLE", ("3",)) not in found
    # Tag 9 requires consent, and the paused tag 7 is ignored by consent and GA4 checks.
    assert not any(r == "CONSENT_NOT_ENFORCED" and ("9" in ids or "7" in ids) for r, ids in found)
    # Spaces in an event name are a medium (inconsistent) issue, not a high (dropped) one.
    assert next(f for f in result["findings"] if f["rule"] == "GA4_EVENT_NAME_INVALID")["severity"] == "medium"
    # Ordered by severity, critical first; score is bounded.
    assert result["findings"][0]["severity"] == "critical"
    assert 0 < result["score"] < 60


def test_paused_tags_and_consent_tools_are_not_flagged(version):
    """Reported in the field: paused tags were flagged as 'high', and the consent tool itself as 'no consent check'."""
    version["tag"].append({"tagId": "20", "name": "Old Event", "type": "gaawe", "paused": True,
                           "parameter": [{"type": "template", "key": "eventName", "value": "Old Event Name With Spaces"}]})
    version["tag"].append({"tagId": "21", "name": "TRUSTARC GTM Tag", "type": "html", "firingTriggerId": ["2147479553"],
                           "parameter": [{"type": "template", "key": "html", "value": "<script src='https://consent.trustarc.com/notice?domain=x'></script>"}]})
    result = run_audit(Container(version), NAMING)
    invalid = next(f for f in result["findings"] if f["rule"] == "GA4_EVENT_NAME_INVALID")
    assert "20" not in invalid["entity_ids"]
    consent = next(f for f in result["findings"] if f["rule"] == "CONSENT_NOT_ENFORCED")
    assert "21" not in consent["entity_ids"]
    assert any(f["rule"] == "CMP_VIA_GTM" and f["entity_ids"] == ["21"] for f in result["findings"])


def test_clean_container_scores_100():
    version = {
        "containerVersionId": "1",
        "container": {"name": "Clean", "publicId": "GTM-CLEAN"},
        "tag": [
            {"tagId": "1", "name": "GA4 - Google Tag - All Pages", "type": "googtag",
             "parameter": [{"type": "template", "key": "tagId", "value": "G-ABC123"}], "firingTriggerId": ["2147479553"]},
            {"tagId": "2", "name": "GA4 - Event - sign_up", "type": "gaawe", "firingTriggerId": ["5"],
             "parameter": [{"type": "template", "key": "eventName", "value": "sign_up"},
                           {"type": "template", "key": "measurementIdOverride", "value": "{{Constant - GA4 ID}}"}]},
        ],
        "trigger": [{"triggerId": "5", "name": "CE - sign_up", "type": "customEvent",
                     "customEventFilter": [{"type": "equals", "parameter": [{"type": "template", "key": "arg0", "value": "{{_event}}"},
                                                                            {"type": "template", "key": "arg1", "value": "sign_up"}]}]}],
        "variable": [{"variableId": "7", "name": "Constant - GA4 ID", "type": "c", "parameter": [{"type": "template", "key": "value", "value": "G-ABC123"}]}],
    }
    result = run_audit(Container(version), NAMING)
    assert result["findings"] == []
    assert result["score"] == 100


def test_sanitize_event_name():
    assert sanitize_event_name("Sign Up") == "sign_up"
    assert sanitize_event_name("formSubmit") == "form_submit"
    assert sanitize_event_name("ga_click") == "click"
    assert sanitize_event_name("1st click") == "event_1st_click"
    assert sanitize_event_name("x" * 60) == "x" * 40
    assert sanitize_event_name("!!!") is None
