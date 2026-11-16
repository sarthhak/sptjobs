"""Read-only view over a GTM container version (the JSON from versions.live / an export)."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

ALL_PAGES = "2147479553"
CONSENT_INIT = "2147479572"
INITIALIZATION = "2147479573"
BUILT_IN_TRIGGERS = {ALL_PAGES: "All Pages", CONSENT_INIT: "Consent Initialization - All Pages", INITIALIZATION: "Initialization - All Pages"}

VAR_REF = re.compile(r"\{\{([^{}]+)\}\}")

# Fields that identify an entity rather than describe it; ignored when comparing entities.
META_KEYS = {"accountId", "containerId", "workspaceId", "fingerprint", "path", "tagManagerUrl"}
IDENTITY_KEYS = META_KEYS | {"tagId", "triggerId", "variableId", "name", "notes", "parentFolderId", "monitoringMetadata"}


def strings_in(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings_in(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings_in(v)


def var_refs(obj: Any) -> set[str]:
    refs = set()
    for s in strings_in(obj):
        refs.update(m.strip() for m in VAR_REF.findall(s))
    return refs


def normalize(entity: dict) -> dict:
    """Entity without volatile metadata - used to detect changes between audit and apply."""
    return {k: v for k, v in entity.items() if k not in META_KEYS}


def signature(entity: dict) -> str:
    """Stable fingerprint of what an entity *does*, ignoring its id and name."""
    return json.dumps({k: v for k, v in entity.items() if k not in IDENTITY_KEYS}, sort_keys=True)


def param(entity: dict, key: str, default: Any = None) -> Any:
    for p in entity.get("parameter", []) or []:
        if p.get("key") == key:
            if p.get("type") == "list":
                return p.get("list", [])
            if p.get("type") == "map":
                return p.get("map", [])
            return p.get("value", default)
    return default


def map_value(m: dict, key: str) -> Any:
    for p in m.get("map", []) or []:
        if p.get("key") == key:
            return p.get("value")
    return None


class Container:
    def __init__(self, version: dict):
        self.raw = version
        self.tags: list[dict] = version.get("tag", []) or []
        self.triggers: list[dict] = version.get("trigger", []) or []
        self.variables: list[dict] = version.get("variable", []) or []
        self.builtins: list[dict] = version.get("builtInVariable", []) or []
        self.folders: list[dict] = version.get("folder", []) or []
        self.container: dict = version.get("container", {}) or {}
        self.version_id: str | None = version.get("containerVersionId")
        self.tag_by_id = {t["tagId"]: t for t in self.tags}
        self.trigger_by_id = {t["triggerId"]: t for t in self.triggers}
        self.var_by_id = {v["variableId"]: v for v in self.variables}
        self.var_by_name = {v["name"]: v for v in self.variables}
        self.builtin_names = {b.get("name") for b in self.builtins}

    @property
    def name(self) -> str:
        return self.container.get("name") or self.container.get("publicId") or "container"

    def entity(self, kind: str, entity_id: str) -> dict | None:
        return {"tag": self.tag_by_id, "trigger": self.trigger_by_id, "variable": self.var_by_id}[kind].get(entity_id)

    def trigger_name(self, trigger_id: str) -> str:
        if trigger_id in BUILT_IN_TRIGGERS:
            return BUILT_IN_TRIGGERS[trigger_id]
        t = self.trigger_by_id.get(trigger_id)
        return t["name"] if t else f"missing trigger {trigger_id}"

    def referenced_trigger_ids(self) -> set[str]:
        ids: set[str] = set()
        for t in self.tags:
            ids.update(t.get("firingTriggerId", []) or [])
            ids.update(t.get("blockingTriggerId", []) or [])
        for trg in self.triggers:
            if trg.get("type") == "triggerGroup":
                for item in param(trg, "triggerIds", []) or []:
                    if item.get("value"):
                        ids.add(item["value"])
        return ids

    def summary(self) -> dict:
        return {
            "container": self.name,
            "public_id": self.container.get("publicId"),
            "version_id": self.version_id,
            "tags": [{"id": t["tagId"], "name": t["name"], "type": t["type"], "paused": bool(t.get("paused")),
                      "fires_on": [self.trigger_name(i) for i in t.get("firingTriggerId", []) or []]} for t in self.tags],
            "triggers": [{"id": t["triggerId"], "name": t["name"], "type": t["type"]} for t in self.triggers],
            "variables": [{"id": v["variableId"], "name": v["name"], "type": v["type"]} for v in self.variables],
            "built_in_variables": sorted(n for n in self.builtin_names if n),
        }
