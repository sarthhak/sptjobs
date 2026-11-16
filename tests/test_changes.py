import pytest

from app.audit.apply import ApplyError, apply_changeset, publish_workspace, rollback
from app.audit.container import Container, param
from app.audit.fixes import OpError, build_changeset, custom_op
from app.audit.rules import run_audit


def audit(version):
    return run_audit(Container(version), r"^[^-]+ - [^-]+ - .+$")


def fid(result, rule, ids=None):
    for f in result["findings"]:
        if f["rule"] == rule and (ids is None or f["entity_ids"] == ids):
            return f["id"]
    raise AssertionError(f"no {rule} finding")


def test_fix_ops(version):
    c, result = Container(version), audit(version)
    ops, errors = build_changeset(c, result["findings"], [
        fid(result, "DUPLICATE_TAG"), fid(result, "DUPLICATE_TRIGGER"), fid(result, "GA4_NO_MEASUREMENT_ID"),
        fid(result, "MISSING_CONVERSION_LINKER"), fid(result, "LEGACY_UA_TAG"), fid(result, "UNUSED_TRIGGER"),
    ], [])
    assert errors == []
    summary = {(o["op"], o["kind"], o["entity_id"]) for o in ops}
    assert ("delete", "tag", "3") in summary            # duplicate tag removed, original kept
    assert ("update", "tag", "4") in summary            # repointed from trigger 11 to 10
    assert ("delete", "trigger", "11") in summary
    assert ("delete", "trigger", "12") in summary
    assert ("update", "tag", "6") in summary            # UA paused
    assert ("create", "tag", None) in summary           # conversion linker
    t4 = next(o for o in ops if o["entity_id"] == "4")
    assert t4["patch"] == {"firingTriggerId": ["10"]}
    t5 = next(o for o in ops if o["entity_id"] == "5")
    assert param({"parameter": t5["patch"]["parameter"]}, "measurementIdOverride") == "G-ABC123"


def test_conflicting_fixes_are_reported(version):
    c, result = Container(version), audit(version)
    ops, errors = build_changeset(c, result["findings"], [fid(result, "GA4_EVENT_NAME_INVALID"), fid(result, "HARDCODED_MEASUREMENT_ID")], [])
    # Both edit tag 4's parameter list; the second is skipped rather than silently overwriting the first.
    assert any("tag 'GA4 Event Sign Up'" in e for e in errors)
    t4 = [o for o in ops if o["entity_id"] == "4"]
    assert len(t4) == 1 and param({"parameter": t4[0]["patch"]["parameter"]}, "eventName") == "sign_up"


def test_non_fixable_and_custom_op_validation(version):
    c, result = Container(version), audit(version)
    ops, errors = build_changeset(c, result["findings"], [fid(result, "CONSENT_NOT_SET")], [])
    assert ops == [] and "no automatic fix" in errors[0]
    with pytest.raises(OpError):
        custom_op(c, {"op": "update", "kind": "tag", "entity_id": "1", "patch": {"type": "html"}})
    with pytest.raises(OpError):
        custom_op(c, {"op": "delete", "kind": "tag", "entity_id": "404"})
    with pytest.raises(OpError):
        custom_op(c, {"op": "create", "kind": "tag", "body": {"name": "GA4 - Google Tag - All Pages", "type": "googtag"}})
    op = custom_op(c, {"op": "update", "kind": "tag", "entity_id": "8", "patch": {"paused": True}, "reason": "pause pixel"})
    assert op["before"]["name"] == "HTML - Pixel - all events"


def test_apply_publish_rollback(version, fake_gtm):
    c, result = Container(version), audit(version)
    ops, _ = build_changeset(c, result["findings"], [fid(result, "DUPLICATE_TAG"), fid(result, "HARDCODED_MEASUREMENT_ID"),
                                                    fid(result, "UNUSED_VARIABLE", ["2"])], [])
    res = apply_changeset(fake_gtm, fake_gtm.container_path, 7, ops, "42")
    ws = res["workspace_path"]
    tags = {t["tagId"]: t for t in fake_gtm.workspaces[ws]["tag"]}
    assert "3" not in tags
    assert param(tags["1"], "tagId") == "{{Constant - GA4 Measurement ID - G-ABC123}}"
    assert any(v["name"] == "Constant - GA4 Measurement ID - G-ABC123" for v in fake_gtm.workspaces[ws]["variable"])
    # Order: creates before updates before deletes.
    kinds = [call[0] for call in fake_gtm.calls if call[0] in ("create", "update", "delete")]
    assert kinds == sorted(kinds, key=["create", "update", "delete"].index)

    pub = publish_workspace(fake_gtm, fake_gtm.container_path, ws, "AI change set #7", "notes")
    assert fake_gtm.live_id == pub["version_id"] == "43"
    rollback(fake_gtm, res["previous_live_version_path"])
    assert fake_gtm.live_id == "42"


def test_apply_aborts_when_entity_changed(version, fake_gtm):
    c, result = Container(version), audit(version)
    ops, _ = build_changeset(c, result["findings"], [fid(result, "LEGACY_UA_TAG")], [])
    # Someone edits the UA tag in GTM after the audit.
    fake_gtm.versions["42"]["tag"][5]["name"] = "UA - renamed"
    with pytest.raises(ApplyError, match="changed in GTM"):
        apply_changeset(fake_gtm, fake_gtm.container_path, 1, ops, "42")
    assert fake_gtm.workspaces == {}  # workspace cleaned up
    assert ("delete_workspace",) == fake_gtm.calls[-1][:1]


def test_apply_refuses_unpublished_latest_version(version, fake_gtm):
    fake_gtm.latest_id = "43"
    with pytest.raises(ApplyError, match="unreleased version"):
        apply_changeset(fake_gtm, fake_gtm.container_path, 1, [], "42")
    assert not any(call[0] == "create_workspace" for call in fake_gtm.calls)


def test_apply_cleans_up_on_api_error(version, fake_gtm):
    c, result = Container(version), audit(version)
    ops, _ = build_changeset(c, result["findings"], [fid(result, "LEGACY_UA_TAG")], [])
    fake_gtm.fail_on = ("update", "tag")
    with pytest.raises(ApplyError):
        apply_changeset(fake_gtm, fake_gtm.container_path, 1, ops, "42")
    assert fake_gtm.workspaces == {}
