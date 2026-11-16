"""Thin wrapper over the Google Tag Manager API v2.

Paths follow the API: accounts/{a}/containers/{c}[/workspaces/{w}[/tags/{id}]].
"""

from __future__ import annotations

import threading
import time
from typing import Any

from .google_auth import GoogleCredentials

KINDS = {
    "tag": ("tags", "tagId"),
    "trigger": ("triggers", "triggerId"),
    "variable": ("variables", "variableId"),
}


class GTMClient:
    def __init__(self, creds: GoogleCredentials, write_interval: float = 4.0):
        self._creds = creds
        self._write_interval = write_interval
        self._write_lock = threading.Lock()
        self._last_write = 0.0

    # -- helpers -------------------------------------------------------------
    def _svc(self):
        return self._creds.build("tagmanager", "v2")

    def _run(self, request) -> Any:
        return request.execute(num_retries=6)

    def _write(self, request) -> Any:
        with self._write_lock:
            wait = self._write_interval - (time.monotonic() - self._last_write)
            if wait > 0:
                time.sleep(wait)
            try:
                return self._run(request)
            finally:
                self._last_write = time.monotonic()

    @staticmethod
    def _collection(ws, kind: str):
        name, _ = KINDS[kind]
        return getattr(ws, name)()

    # -- read ----------------------------------------------------------------
    def list_containers(self) -> list[dict]:
        svc = self._svc()
        out = []
        accounts = self._run(svc.accounts().list()).get("account", [])
        for acc in accounts:
            resp = self._run(svc.accounts().containers().list(parent=acc["path"]))
            for c in resp.get("container", []):
                out.append({
                    "account_name": acc.get("name"),
                    "container_name": c.get("name"),
                    "public_id": c.get("publicId"),
                    "container_path": c["path"],
                    "usage_context": c.get("usageContext", []),
                })
        return out

    def get_live_version(self, container_path: str) -> dict:
        return self._run(self._svc().accounts().containers().versions().live(parent=container_path))

    def get_latest_version_header(self, container_path: str) -> dict:
        return self._run(self._svc().accounts().containers().version_headers().latest(parent=container_path))

    def list_workspaces(self, container_path: str) -> list[dict]:
        resp = self._run(self._svc().accounts().containers().workspaces().list(parent=container_path))
        return resp.get("workspace", [])

    def get_workspace_status(self, workspace_path: str) -> dict:
        return self._run(self._svc().accounts().containers().workspaces().getStatus(path=workspace_path))

    def get_entity(self, workspace_path: str, kind: str, entity_id: str) -> dict:
        ws = self._svc().accounts().containers().workspaces()
        name, _ = KINDS[kind]
        return self._run(self._collection(ws, kind).get(path=f"{workspace_path}/{name}/{entity_id}"))

    # -- write ---------------------------------------------------------------
    def create_workspace(self, container_path: str, name: str, description: str = "") -> dict:
        ws = self._svc().accounts().containers().workspaces()
        return self._write(ws.create(parent=container_path, body={"name": name, "description": description}))

    def delete_workspace(self, workspace_path: str) -> None:
        self._write(self._svc().accounts().containers().workspaces().delete(path=workspace_path))

    def create_entity(self, workspace_path: str, kind: str, body: dict) -> dict:
        ws = self._svc().accounts().containers().workspaces()
        return self._write(self._collection(ws, kind).create(parent=workspace_path, body=body))

    def update_entity(self, workspace_path: str, kind: str, entity_id: str, body: dict, fingerprint: str) -> dict:
        ws = self._svc().accounts().containers().workspaces()
        name, _ = KINDS[kind]
        path = f"{workspace_path}/{name}/{entity_id}"
        return self._write(self._collection(ws, kind).update(path=path, fingerprint=fingerprint, body=body))

    def delete_entity(self, workspace_path: str, kind: str, entity_id: str) -> None:
        ws = self._svc().accounts().containers().workspaces()
        name, _ = KINDS[kind]
        self._write(self._collection(ws, kind).delete(path=f"{workspace_path}/{name}/{entity_id}"))

    def create_version(self, workspace_path: str, name: str, notes: str) -> dict:
        ws = self._svc().accounts().containers().workspaces()
        return self._write(ws.create_version(path=workspace_path, body={"name": name, "notes": notes}))

    def publish_version(self, version_path: str) -> dict:
        return self._write(self._svc().accounts().containers().versions().publish(path=version_path))
