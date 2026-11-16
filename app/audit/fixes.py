"""Turns audit findings (or hand-written operations) into a change set: a list of operations.

An operation is a plain dict:
  {"op": "create"|"update"|"delete", "kind": "tag"|"trigger"|"variable",
   "entity_id": str|None, "name": str, "patch": {...} (update), "body": {...} (create),
   "before": {...} (update/delete: the entity as it was when the change was proposed),
   "reason": str, "finding_id": str|None}

Nothing here talks to GTM. Applying is done by apply.py, and only after a person approves.
"""

from __future__ import annotations

import copy

from .container import ALL_PAGES, Container, normalize, param
from .rules import ga4_measurement_ids, sanitize_event_name

KINDS = ("tag", "trigger", "variable")
UPDATABLE_FIELDS = {
    "tag": {"name", "parameter", "firingTriggerId", "blockingTriggerId", "paused", "consentSettings", "notes",
            "tagFiringOption", "setupTag", "teardownTag", "priority", "parentFolderId", "liveOnly"},
    "trigger": {"name", "filter", "customEventFilter", "autoEventFilter", "parameter", "notes", "parentFolderId",
                "waitForTags", "checkValidation", "waitForTagsTimeout", "uniqueTriggerId", "eventName"},
    "variable": {"name", "parameter", "notes", "parentFolderId", "formatValue", "disablingTriggerId", "enablingTriggerId"},
}


class OpError(ValueError):
    pass


def _set_param(params: list[dict], key: str, value: str, ptype: str = "template") -> list[dict]:
    params = copy.deepcopy(params or [])
    for p in params:
        if p.get("key") == key:
            p["value"] = value
            p["type"] = p.get("type", ptype)
            return params
    params.append({"type": ptype, "key": key, "value": value})
    return params


def op_update(c: Container, kind: str, entity_id: str, patch: dict, reason: str, finding_id: str | None = None) -> dict:
    ent = c.entity(kind, entity_id)
    if ent is None:
        raise OpError(f"{kind} {entity_id} does not exist in the audited version")
    bad = set(patch) - UPDATABLE_FIELDS[kind]
    if bad:
        raise OpError(f"Cannot change {sorted(bad)} on a {kind}; allowed: {sorted(UPDATABLE_FIELDS[kind])}")
    return {"op": "update", "kind": kind, "entity_id": entity_id, "name": ent["name"], "patch": patch,
            "before": normalize(ent), "reason": reason, "finding_id": finding_id}


def op_delete(c: Container, kind: str, entity_id: str, reason: str, finding_id: str | None = None) -> dict:
    ent = c.entity(kind, entity_id)
    if ent is None:
        raise OpError(f"{kind} {entity_id} does not exist in the audited version")
    return {"op": "delete", "kind": kind, "entity_id": entity_id, "name": ent["name"], "before": normalize(ent),
            "reason": reason, "finding_id": finding_id}


def op_create(c: Container, kind: str, body: dict, reason: str, finding_id: str | None = None) -> dict:
    if not body.get("name") or not body.get("type"):
        raise OpError("A new entity needs at least 'name' and 'type'")
    existing = {"tag": c.tags, "trigger": c.triggers, "variable": c.variables}[kind]
    if any(e["name"] == body["name"] for e in existing):
        raise OpError(f"A {kind} named '{body['name']}' already exists")
    return {"op": "create", "kind": kind, "entity_id": None, "name": body["name"], "body": body,
            "reason": reason, "finding_id": finding_id}


# --- fixes per rule ---------------------------------------------------------

def fix_ops(c: Container, finding: dict) -> list[dict]:
    rule, fid, ids = finding["rule"], finding["id"], finding["entity_ids"]
    if not finding.get("fixable"):
        raise OpError(f"Finding {fid} has no automatic fix; describe custom operations instead")

    if rule == "UNUSED_TRIGGER":
        return [op_delete(c, "trigger", ids[0], "Trigger is not used by any tag", fid)]
    if rule == "UNUSED_VARIABLE":
        return [op_delete(c, "variable", ids[0], "Variable is not referenced anywhere", fid)]
    if rule == "PAUSED_TAG":
        return [op_delete(c, "tag", ids[0], "Tag is paused and no longer needed", fid)]
    if rule in ("TAG_NO_TRIGGER", "LEGACY_UA_TAG"):
        return [op_update(c, "tag", ids[0], {"paused": True}, finding["title"] + " - pausing it", fid)]
    if rule == "DUPLICATE_TAG":
        keep = ids[0]
        return [op_delete(c, "tag", i, f"Duplicate of tag {keep}", fid) for i in ids[1:]]
    if rule == "DUPLICATE_TRIGGER":
        keep, dupes = ids[0], set(ids[1:])
        ops = []
        for t in c.tags:
            patch = {}
            for key in ("firingTriggerId", "blockingTriggerId"):
                cur = t.get(key) or []
                if dupes & set(cur):
                    new = []
                    for i in cur:
                        j = keep if i in dupes else i
                        if j not in new:
                            new.append(j)
                    patch[key] = new
            if patch:
                ops.append(op_update(c, "tag", t["tagId"], patch, f"Point to trigger {keep} instead of its duplicates", fid))
        ops += [op_delete(c, "trigger", i, f"Duplicate of trigger {keep}", fid) for i in sorted(dupes)]
        return ops
    if rule == "GA4_EVENT_NAME_INVALID":
        tag = c.tag_by_id[ids[0]]
        new = sanitize_event_name(param(tag, "eventName"))
        if not new:
            raise OpError("Could not derive a valid event name")
        return [op_update(c, "tag", ids[0], {"parameter": _set_param(tag["parameter"], "eventName", new)},
                          f"Rename event to '{new}'", fid)]
    if rule == "GA4_NO_MEASUREMENT_ID":
        mids = ga4_measurement_ids(c)
        if len(mids) != 1:
            raise OpError("Need exactly one GA4 measurement ID in the container to fill it in automatically")
        tag = c.tag_by_id[ids[0]]
        return [op_update(c, "tag", ids[0], {"parameter": _set_param(tag.get("parameter"), "measurementIdOverride", next(iter(mids)))},
                          "Set the missing measurement ID", fid)]
    if rule == "ECOMMERCE_NO_ITEMS":
        tag = c.tag_by_id[ids[0]]
        params = _set_param(tag.get("parameter"), "sendEcommerceData", "true", "boolean")
        params = _set_param(params, "getEcommerceDataFrom", "dataLayer")
        return [op_update(c, "tag", ids[0], {"parameter": params}, "Send ecommerce data from the dataLayer", fid)]
    if rule == "MISSING_CONVERSION_LINKER":
        body = {"name": "Google Ads - Conversion Linker - All Pages", "type": "gclidw", "firingTriggerId": [ALL_PAGES],
                "parameter": [{"type": "boolean", "key": "enableCrossDomain", "value": "false"},
                              {"type": "boolean", "key": "enableUrlPassthrough", "value": "false"},
                              {"type": "boolean", "key": "enableCookieOverrides", "value": "false"}],
                "tagFiringOption": "oncePerEvent"}
        return [op_create(c, "tag", body, "Add a Conversion Linker so ad click IDs are kept", fid)]
    if rule == "HARDCODED_MEASUREMENT_ID":
        mid = fid.split(":", 1)[1]
        var_name = f"Constant - GA4 Measurement ID - {mid}"
        ops = []
        if var_name not in c.var_by_name:
            ops.append(op_create(c, "variable", {"name": var_name, "type": "c",
                                                 "parameter": [{"type": "template", "key": "value", "value": mid}]},
                                 f"Store {mid} in one place", fid))
        for tid in ids:
            tag = c.tag_by_id[tid]
            params = copy.deepcopy(tag["parameter"])
            for p in params:
                if p.get("key") in ("tagId", "measurementIdOverride") and p.get("value") == mid:
                    p["value"] = "{{" + var_name + "}}"
            ops.append(op_update(c, "tag", tid, {"parameter": params}, f"Use {{{{{var_name}}}}} instead of the typed ID", fid))
        return ops
    raise OpError(f"No automatic fix for rule {rule}")


def custom_op(c: Container, spec: dict) -> dict:
    """Validates an operation written by the assistant or a person."""
    op, kind = spec.get("op"), spec.get("kind")
    if kind not in KINDS:
        raise OpError(f"kind must be one of {KINDS}")
    reason = spec.get("reason") or "Custom change"
    if op == "update":
        return op_update(c, kind, str(spec.get("entity_id")), spec.get("patch") or {}, reason)
    if op == "delete":
        return op_delete(c, kind, str(spec.get("entity_id")), reason)
    if op == "create":
        return op_create(c, kind, spec.get("body") or {}, reason)
    raise OpError("op must be create, update or delete")


def build_changeset(c: Container, findings: list[dict], finding_ids: list[str], custom: list[dict]) -> tuple[list[dict], list[str]]:
    """Returns (ops, errors). Duplicate operations on the same entity are rejected."""
    by_id = {f["id"]: f for f in findings}
    ops, errors = [], []
    for fid in finding_ids:
        f = by_id.get(fid)
        if not f:
            errors.append(f"Unknown finding id {fid}")
            continue
        try:
            ops.extend(fix_ops(c, f))
        except OpError as e:
            errors.append(f"{fid}: {e}")
    for spec in custom:
        try:
            ops.append(custom_op(c, spec))
        except OpError as e:
            errors.append(f"custom op {spec}: {e}")
    seen: dict[tuple, int] = {}
    deduped = []
    for o in ops:
        if o["op"] == "create":
            deduped.append(o)
            continue
        key = (o["kind"], o["entity_id"])
        if key in seen:
            prev = deduped[seen[key]]
            if prev["op"] == "update" and o["op"] == "update":
                clash = [k for k in set(prev["patch"]) & set(o["patch"]) if prev["patch"][k] != o["patch"][k]]
                if clash:
                    errors.append(f"Two changes edit {clash} on {o['kind']} '{o['name']}'; "
                                  f"skipped '{o['reason']}' - propose it again after this change set is published")
                    continue
                prev["patch"].update(o["patch"])
                prev["reason"] += "; " + o["reason"]
            elif o["op"] == "delete":
                deduped[seen[key]] = o  # deleting wins over updating
            continue
        seen[key] = len(deduped)
        deduped.append(o)
    return deduped, errors
