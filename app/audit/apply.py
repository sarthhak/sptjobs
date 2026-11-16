"""Applies an approved change set to a new GTM workspace, then (separately) versions and publishes it.

Safety checks:
- Refuses if the latest container version is not the live one (publishing would also push someone's
  unpublished version).
- Before touching an entity, re-reads it and aborts if it changed since the change set was proposed.
- On any failure the new workspace is deleted so it doesn't use up one of the container's workspace slots.
"""

from __future__ import annotations

from typing import Any

from .container import normalize

ORDER = {("create", "variable"): 0, ("create", "trigger"): 1, ("create", "tag"): 2,
         ("update", "variable"): 3, ("update", "trigger"): 4, ("update", "tag"): 5,
         ("delete", "tag"): 6, ("delete", "trigger"): 7, ("delete", "variable"): 8}


class ApplyError(RuntimeError):
    pass


def _strip(entity: dict) -> dict:
    n = normalize(entity)
    n.pop("workspaceId", None)
    return n


def check_live_is_latest(gtm, container_path: str) -> dict:
    live = gtm.get_live_version(container_path)
    latest = gtm.get_latest_version_header(container_path)
    if str(latest.get("containerVersionId")) != str(live.get("containerVersionId")):
        raise ApplyError(
            f"Version {latest.get('containerVersionId')} exists but version {live.get('containerVersionId')} is live. "
            "Publishing from a new workspace would also publish that unreleased version. Publish or discard it in GTM first."
        )
    return live


def apply_changeset(gtm, container_path: str, changeset_id: int, ops: list[dict], audited_version_id: str | None) -> dict[str, Any]:
    live = check_live_is_latest(gtm, container_path)
    log: list[str] = []
    if audited_version_id and str(live.get("containerVersionId")) != str(audited_version_id):
        log.append(f"Note: audit ran on version {audited_version_id}, live is now {live.get('containerVersionId')}. "
                   "Every changed item is re-checked below.")
    ws = gtm.create_workspace(container_path, f"AI change set #{changeset_id}",
                              "Created by the Analytics Expert app after approval.")
    ws_path = ws["path"]
    log.append(f"Created workspace {ws.get('name')}")
    try:
        for op in sorted(ops, key=lambda o: ORDER[(o["op"], o["kind"])]):
            if op["op"] == "create":
                created = gtm.create_entity(ws_path, op["kind"], op["body"])
                log.append(f"Created {op['kind']} '{op['name']}'")
                op["result_id"] = created.get(f"{op['kind']}Id")
                continue
            current = gtm.get_entity(ws_path, op["kind"], op["entity_id"])
            if _strip(current) != _strip(op["before"]):
                raise ApplyError(f"{op['kind']} '{op['name']}' was changed in GTM after this change set was proposed. "
                                 "Run the audit again and propose a fresh change set.")
            if op["op"] == "update":
                body = {**current, **op["patch"]}
                gtm.update_entity(ws_path, op["kind"], op["entity_id"], body, current["fingerprint"])
                log.append(f"Updated {op['kind']} '{op['name']}': {', '.join(op['patch'])}")
            else:
                gtm.delete_entity(ws_path, op["kind"], op["entity_id"])
                log.append(f"Deleted {op['kind']} '{op['name']}'")
        status = gtm.get_workspace_status(ws_path)
        if status.get("mergeConflict"):
            raise ApplyError("The workspace has merge conflicts.")
    except Exception as exc:
        log.append(f"Failed: {exc}")
        try:
            gtm.delete_workspace(ws_path)
            log.append("Deleted the workspace; nothing was changed in GTM.")
        except Exception as cleanup_exc:  # noqa: BLE001
            log.append(f"Could not delete workspace {ws_path}: {cleanup_exc}")
        raise ApplyError("; ".join(log[-3:])) from exc
    return {"workspace_path": ws_path, "workspace_url": ws.get("tagManagerUrl"),
            "previous_live_version_path": live.get("path"), "log": log}


def publish_workspace(gtm, container_path: str, workspace_path: str, name: str, notes: str) -> dict[str, Any]:
    check_live_is_latest(gtm, container_path)
    created = gtm.create_version(workspace_path, name, notes)
    if created.get("compilerError"):
        raise ApplyError("GTM reported a compiler error; the version was not created. Open the workspace in GTM to see why.")
    if created.get("syncStatus", {}).get("mergeConflict"):
        raise ApplyError("Workspace is out of sync with the container; discard it and re-apply.")
    version = created["containerVersion"]
    gtm.publish_version(version["path"])
    return {"version_path": version["path"], "version_id": version.get("containerVersionId"),
            "version_url": version.get("tagManagerUrl")}


def rollback(gtm, version_path: str) -> dict:
    """Re-publishes an earlier version."""
    gtm.publish_version(version_path)
    return {"published": version_path}
