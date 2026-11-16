"""SQLite storage for conversations, query results, charts, audits, change sets, dashboards and reports."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY, title TEXT, created REAL);
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, conversation_id INTEGER, role TEXT, content TEXT, created REAL);
CREATE TABLE IF NOT EXISTS results (id INTEGER PRIMARY KEY, conversation_id INTEGER, spec TEXT, data TEXT, created REAL);
CREATE TABLE IF NOT EXISTS charts (id INTEGER PRIMARY KEY, conversation_id INTEGER, spec TEXT, created REAL);
CREATE TABLE IF NOT EXISTS audits (id INTEGER PRIMARY KEY, container_path TEXT, container_name TEXT, version_id TEXT,
    score INTEGER, result TEXT, created REAL);
CREATE TABLE IF NOT EXISTS changesets (id INTEGER PRIMARY KEY, container_path TEXT, audit_id INTEGER, version_id TEXT,
    status TEXT, summary TEXT, ops TEXT, errors TEXT, log TEXT, workspace_path TEXT, workspace_url TEXT,
    version_path TEXT, previous_version_path TEXT, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS dashboards (id INTEGER PRIMARY KEY, name TEXT, description TEXT, property TEXT, widgets TEXT, created REAL);
CREATE TABLE IF NOT EXISTS ga4_audits (id INTEGER PRIMARY KEY, property TEXT, property_name TEXT, score INTEGER, result TEXT, created REAL);
CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY, source TEXT, conversation_id INTEGER, model TEXT, input_tokens INTEGER,
    output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, cost_usd REAL, created REAL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY, conversation_id INTEGER, title TEXT, markdown TEXT, created REAL);
"""

JSON_COLUMNS = {"content", "spec", "data", "result", "ops", "errors", "log", "widgets"}


class Store:
    def __init__(self, path: Path | str):
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    # -- generic helpers -----------------------------------------------------
    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        for k in JSON_COLUMNS & d.keys():
            if d[k] is not None:
                d[k] = json.loads(d[k])
        return d

    def _insert(self, table: str, **values: Any) -> int:
        values.setdefault("created", time.time())
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        data = [json.dumps(v) if k in JSON_COLUMNS else v for k, v in values.items()]
        with self._lock:
            cur = self._db.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", data)
            self._db.commit()
            return cur.lastrowid

    def _update(self, table: str, row_id: int, **values: Any) -> None:
        sets = ", ".join(f"{k} = ?" for k in values)
        data = [json.dumps(v) if k in JSON_COLUMNS else v for k, v in values.items()]
        with self._lock:
            self._db.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*data, row_id])
            self._db.commit()

    def _get(self, table: str, row_id: int) -> dict | None:
        with self._lock:
            return self._row(self._db.execute(f"SELECT * FROM {table} WHERE id = ?", [row_id]).fetchone())

    def _list(self, sql: str, args: list | None = None) -> list[dict]:
        with self._lock:
            return [self._row(r) for r in self._db.execute(sql, args or []).fetchall()]

    def _delete(self, table: str, row_id: int) -> None:
        with self._lock:
            self._db.execute(f"DELETE FROM {table} WHERE id = ?", [row_id])
            self._db.commit()

    # -- conversations -------------------------------------------------------
    def create_conversation(self, title: str) -> int:
        return self._insert("conversations", title=title)

    def rename_conversation(self, cid: int, title: str) -> None:
        self._update("conversations", cid, title=title)

    def get_conversation(self, cid: int) -> dict | None:
        return self._get("conversations", cid)

    def list_conversations(self) -> list[dict]:
        return self._list("SELECT * FROM conversations ORDER BY id DESC")

    def delete_conversation(self, cid: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM messages WHERE conversation_id = ?", [cid])
            self._db.commit()
        self._delete("conversations", cid)

    def append_message(self, cid: int, role: str, content: Any) -> int:
        return self._insert("messages", conversation_id=cid, role=role, content=content)

    def messages(self, cid: int) -> list[dict]:
        return self._list("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", [cid])

    # -- results & charts ----------------------------------------------------
    def save_result(self, cid: int | None, spec: dict, data: dict) -> int:
        return self._insert("results", conversation_id=cid, spec=spec, data=data)

    def get_result(self, rid: int) -> dict | None:
        return self._get("results", rid)

    def save_chart(self, cid: int | None, spec: dict) -> int:
        return self._insert("charts", conversation_id=cid, spec=spec)

    def get_chart(self, chart_id: int) -> dict | None:
        return self._get("charts", chart_id)

    def charts_for(self, cid: int) -> list[dict]:
        return self._list("SELECT * FROM charts WHERE conversation_id = ? ORDER BY id", [cid])

    # -- audits ----------------------------------------------------------------
    def save_audit(self, container_path: str, result: dict) -> int:
        return self._insert("audits", container_path=container_path, container_name=result.get("container"),
                            version_id=result.get("version_id"), score=result.get("score"), result=result)

    def get_audit(self, aid: int) -> dict | None:
        return self._get("audits", aid)

    def list_audits(self) -> list[dict]:
        return self._list("SELECT id, container_path, container_name, version_id, score, created FROM audits ORDER BY id DESC")

    def save_ga4_audit(self, result: dict) -> int:
        return self._insert("ga4_audits", property=result["property"], property_name=result.get("property_name"),
                            score=result.get("score"), result=result)

    def get_ga4_audit(self, aid: int) -> dict | None:
        return self._get("ga4_audits", aid)

    def list_ga4_audits(self) -> list[dict]:
        return self._list("SELECT id, property, property_name, score, created FROM ga4_audits ORDER BY id DESC")

    def update_audit(self, aid: int, result: dict) -> None:
        self._update("audits", aid, result=result, score=result.get("score"))

    # -- usage & settings -------------------------------------------------------------
    def record_usage(self, **values) -> int:
        return self._insert("usage", **values)

    def spend_since(self, ts: float) -> float:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE created >= ?", [ts]).fetchone()
        return float(row[0])

    def spend_by_source(self, ts: float) -> list[dict]:
        return self._list("SELECT source, COUNT(*) AS calls, SUM(input_tokens + cache_read_tokens + cache_write_tokens) AS input_tokens, "
                          "SUM(output_tokens) AS output_tokens, SUM(cost_usd) AS cost_usd FROM usage WHERE created >= ? GROUP BY source", [ts])

    def get_setting(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE key = ?", [key]).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str | None) -> None:
        with self._lock:
            if value is None:
                self._db.execute("DELETE FROM settings WHERE key = ?", [key])
            else:
                self._db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", [key, value])
            self._db.commit()

    # -- change sets -----------------------------------------------------------
    def create_changeset(self, **values: Any) -> int:
        values.setdefault("status", "proposed")
        values.setdefault("log", [])
        values["updated"] = time.time()
        return self._insert("changesets", **values)

    def update_changeset(self, csid: int, **values: Any) -> None:
        values["updated"] = time.time()
        self._update("changesets", csid, **values)

    def get_changeset(self, csid: int) -> dict | None:
        return self._get("changesets", csid)

    def list_changesets(self) -> list[dict]:
        return self._list("SELECT id, container_path, audit_id, status, summary, created, updated FROM changesets ORDER BY id DESC")

    # -- dashboards & reports -------------------------------------------------
    def save_dashboard(self, name: str, description: str, prop: str, widgets: list[dict]) -> int:
        return self._insert("dashboards", name=name, description=description, property=prop, widgets=widgets)

    def get_dashboard(self, did: int) -> dict | None:
        return self._get("dashboards", did)

    def list_dashboards(self) -> list[dict]:
        return self._list("SELECT id, name, description, property, created FROM dashboards ORDER BY id DESC")

    def delete_dashboard(self, did: int) -> None:
        self._delete("dashboards", did)

    def save_report(self, cid: int | None, title: str, markdown: str) -> int:
        return self._insert("reports", conversation_id=cid, title=title, markdown=markdown)

    def get_report(self, rid: int) -> dict | None:
        return self._get("reports", rid)

    def list_reports(self) -> list[dict]:
        return self._list("SELECT id, title, created FROM reports ORDER BY id DESC")
