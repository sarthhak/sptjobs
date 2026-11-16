"""Excel (.xlsx) exports of GTM and GA4 audits."""

from __future__ import annotations

import datetime as dt
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

SEV_FILL = {"critical": "F4C7C3", "high": "FAD7B5", "medium": "FCEBB6", "low": "DDEBC8", "info": "E3E7ED"}
HEADER_FILL = PatternFill("solid", fgColor="2F5BD3")


def _sheet(wb: Workbook, title: str, headers: list[str], rows: list[list], widths: list[int], sev_col: int | None = None):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for row in rows:
        ws.append([("\n".join(v) if isinstance(v, list) else v) for v in row])
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        if sev_col is not None:
            sev = str(row[sev_col].value or "").lower()
            if sev in SEV_FILL:
                row[sev_col].fill = PatternFill("solid", fgColor=SEV_FILL[sev])
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions
    return ws


def _summary(wb: Workbook, title: str, pairs: list[tuple[str, object]]):
    ws = wb.active
    ws.title = "Summary"
    ws.append([title])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])
    for k, v in pairs:
        ws.append([k, v])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 90
    for row in ws.iter_rows(min_row=3):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def _bytes(wb: Workbook) -> bytes:
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _when(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def gtm_audit_xlsx(audit: dict) -> bytes:
    wb = Workbook()
    c = audit["counts"]
    _summary(wb, f"GTM audit - {audit['container']} ({audit.get('public_id')})", [
        ("Container version", audit.get("version_id")), ("Run at", _when(audit["created"])),
        ("Health score", f"{audit['score']} / 100"),
        ("Findings", ", ".join(f"{k}: {v}" for k, v in c.items() if v) or "none"),
        ("Size", f"{audit['totals']['tags']} tags, {audit['totals']['triggers']} triggers, {audit['totals']['variables']} variables"),
        ("How to read this", "Findings lists each problem with its impact and fix. 'Affected items' has one row per tag/trigger/variable "
                             "involved. The Tags, Triggers and Variables sheets are a full inventory of the audited version."),
    ])
    findings = audit["findings"]
    _sheet(wb, "Findings", ["#", "Severity", "Category", "Finding", "What's happening", "Why it matters", "How to fix", "Owner",
                            "Affected items", "Auto-fix"],
           [[i, f["severity"], f.get("category"), f["title"], f["detail"], f.get("impact"), f["recommendation"], f.get("owner"),
             [f"{a['kind']}: {a['name']}" for a in f.get("affected", [])], "Yes" if f.get("fixable") else "No"]
            for i, f in enumerate(findings, 1)],
           [5, 10, 16, 40, 45, 40, 45, 16, 35, 8], sev_col=1)
    _sheet(wb, "Affected items", ["Finding #", "Severity", "Finding", "Item", "Name", "ID", "Type", "Status", "Fires on", "Details"],
           [[i, f["severity"], f["title"], a["kind"], a["name"], a["id"], a["type"], a.get("status"), a.get("triggers"), a.get("details")]
            for i, f in enumerate(findings, 1) for a in f.get("affected", [])],
           [9, 10, 40, 9, 35, 7, 22, 9, 30, 60], sev_col=1)
    inv = audit["inventory"]
    _sheet(wb, "Tags", ["ID", "Name", "Type", "Status", "Fires on", "Blocked by", "Consent check", "Details", "Folder", "Issues"],
           [[t["id"], t["name"], t["type"], t["status"], t["fires_on"], t["blocked_by"], t["consent"], t["details"], t["folder"], t["issues"]]
            for t in inv["tags"]], [7, 35, 22, 9, 30, 22, 13, 60, 16, 45])
    _sheet(wb, "Triggers", ["ID", "Name", "Type", "Conditions", "Used by tags", "Folder", "Issues"],
           [[t["id"], t["name"], t["type"], t["conditions"], t["used_by"], t["folder"], t["issues"]] for t in inv["triggers"]],
           [7, 35, 20, 60, 40, 16, 45])
    _sheet(wb, "Variables", ["ID", "Name", "Type", "Details", "Used by", "Folder", "Issues"],
           [[v["id"], v["name"], v["type"], v["details"], v["used_by"], v["folder"], v["issues"]] for v in inv["variables"]],
           [7, 35, 22, 55, 40, 16, 45])
    return _bytes(wb)


def ga4_audit_xlsx(audit: dict) -> bytes:
    wb = Workbook()
    c = audit["counts"]
    ov = audit.get("overview") or {}
    eng = ov.get("engagementRate")
    _summary(wb, f"GA4 audit - {audit['property_name']}", [
        ("Property", audit["property"]), ("Run at", _when(audit["created"])), ("Data period", audit["period"]),
        ("Health score", f"{audit['score']} / 100"),
        ("Findings", ", ".join(f"{k}: {v}" for k, v in c.items() if v) or "none"),
        ("Sessions", ov.get("sessions")), ("Users", ov.get("totalUsers")),
        ("Engagement rate", f"{eng:.1%}" if isinstance(eng, (int, float)) else eng), ("Key events", ov.get("keyEvents")),
        ("Checks that could not run", "; ".join(f"{k}: {v}" for k, v in (audit.get("errors") or {}).items()) or "none"),
    ])
    _sheet(wb, "Findings", ["#", "Severity", "Category", "Finding", "What's happening", "Why it matters", "How to fix", "Owner", "Evidence"],
           [[i, f["severity"], f["category"], f["title"], f["detail"], f["impact"], f["recommendation"], f["owner"], f.get("evidence") or []]
            for i, f in enumerate(audit["findings"], 1)],
           [5, 10, 16, 40, 45, 40, 50, 16, 50], sev_col=1)
    _sheet(wb, "Property settings", ["Setting", "Value"], [[k, v] for k, v in audit["settings"].items()], [30, 100])
    _sheet(wb, "Channels (28 days)", ["Channel", "Sessions"],
           [[r.get("sessionDefaultChannelGroup"), r.get("sessions")] for r in audit.get("channels") or []], [30, 14])
    _sheet(wb, "Events (28 days)", ["Event name", "Event count"],
           [[r.get("eventName"), r.get("eventCount")] for r in audit.get("top_events") or []], [40, 14])
    return _bytes(wb)
