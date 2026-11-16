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


HEALTH_FILL = {"Red": "F4C7C3", "Amber": "FAD7B5", "Watch": "FCEBB6", "Green": "DDEBC8"}


def _title(ws, title: str, subtitle: str = ""):
    ws.append([title])
    ws.cell(ws.max_row, 1).font = Font(bold=True, size=14)
    if subtitle:
        ws.append([subtitle])
        ws.cell(ws.max_row, 1).font = Font(italic=True, color="5D6676")
    ws.append([])


def _section(ws, text: str):
    ws.append([])
    ws.append([text])
    ws.cell(ws.max_row, 1).font = Font(bold=True, size=12, color="2F5BD3")


def _header_row(ws, headers: list[str]):
    ws.append(headers)
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)


def _table_sheet(wb, title: str, intro: str, headers: list[str], rows: list[list], widths: list[int], sev_col=None, extra_fill=None):
    ws = wb.create_sheet(title)
    _title(ws, title, intro)
    _header_row(ws, headers)
    first = ws.max_row + 1
    for row in rows:
        ws.append([("\n".join(v) if isinstance(v, list) else v) for v in row])
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=first):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        if sev_col is not None:
            sev = str(row[sev_col].value or "").lower()
            if sev in SEV_FILL:
                row[sev_col].fill = PatternFill("solid", fgColor=SEV_FILL[sev])
        if extra_fill:
            col, colors = extra_fill
            val = str(row[col].value or "")
            for key, color in colors.items():
                if val.startswith(key):
                    row[col].fill = PatternFill("solid", fgColor=color)
    ws.freeze_panes = ws.cell(first, 1)
    if rows:
        ws.auto_filter.ref = f"A{first - 1}:{get_column_letter(len(headers))}{ws.max_row}"
    return ws


def gtm_audit_xlsx(audit: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Executive Summary"
    _title(ws, f"GTM container audit - {audit['container']}",
           f"Source: live version {audit.get('version_id')} of {audit.get('public_id')}, read through the GTM API on {_when(audit['created'])}.")
    ws.append(["Container at a glance"])
    ws.cell(ws.max_row, 1).font = Font(bold=True, size=12, color="2F5BD3")
    for k, v in audit["glance"]:
        ws.append([k, v])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
    if audit.get("executive_summary"):
        _section(ws, "Executive summary")
        ws.append([audit["executive_summary"]])
        ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=7)
        ws.row_dimensions[ws.max_row].height = 110
    _section(ws, "Findings scorecard")
    _header_row(ws, ["Area", "Critical", "High", "Medium", "Low", "Total", "Health"])
    for r in audit["scorecard"]:
        ws.append([r["area"], r["critical"], r["high"], r["medium"], r["low"], r["total"], r["health"]])
        ws.cell(ws.max_row, 7).fill = PatternFill("solid", fgColor=HEALTH_FILL.get(r["health"], "FFFFFF"))
        if r["area"] == "Total":
            for cell in ws[ws.max_row]:
                cell.font = Font(bold=True)
    _section(ws, "Top priorities (what to do first)")
    for i, t in enumerate(audit["top_priorities"], 1):
        refs = ", ".join(t.get("finding_refs") or [])
        ws.append([f"{i}. {t['title']}", t["detail"] + (f" ({refs})" if refs else "")])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
    ws.column_dimensions["A"].width = 42
    ws.column_dimensions["B"].width = 100
    for col in "CDEFG":
        ws.column_dimensions[col].width = 10
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    f = audit["findings"]
    _table_sheet(wb, "Findings Log", "Each finding: evidence from the container -> impact -> severity -> effort -> fix -> how to validate.",
                 ["ID", "Area", "Severity", "Effort", "Priority", "Phase", "Finding (headline)", "Detail", "Evidence (IDs)", "Business impact",
                  "Recommended fix", "How to validate", "Owner", "Source", "Status", "Notes"],
                 [[x["ref"], x["area"], x["severity"].title(), x["effort"], x["priority"], x["phase"], x["title"], x["detail"], x["evidence"],
                   x["impact"], x["fix"], x["validate"], x["owner"], x["source"], x["status"], ""] for x in f],
                 [7, 18, 10, 7, 16, 20, 40, 45, 45, 40, 45, 40, 20, 13, 9, 20], sev_col=2)
    _table_sheet(wb, "Action Plan", "Sequenced by phase: consent and data integrity first, then structure, then hygiene.",
                 ["Phase", "Workstream", "Actions", "Findings", "Owner", "Done when"],
                 [[r["phase"], r["workstream"], r["actions"], r["findings"], r["owner"], r["done_when"]] for r in audit["action_plan"]],
                 [22, 20, 80, 22, 25, 60])
    if audit.get("event_model"):
        ws2 = _table_sheet(wb, "Proposed Event Model", "Proposed GA4 event model (from the expert review) replacing the current live GA4 event tags.",
                           ["Current live event(s)", "Proposed event", "Parameters", "Key event?", "Notes"],
                           [[e["current_events"], e["proposed_event"], e["parameters"], e["key_event"], e["notes"]] for e in audit["event_model"]],
                           [45, 25, 50, 15, 50])
        if audit.get("custom_dimensions"):
            _section(ws2, "Custom dimensions to register (event scope)")
            for d in audit["custom_dimensions"]:
                ws2.append([d])
    _table_sheet(wb, "Consent Matrix", "Live tags x firing triggers. An exception only applies on the same GTM event as the firing trigger.",
                 ["Tag ID", "Tag", "Vendor / type", "Firing trigger", "GTM event", "Exception events", "Exception status",
                  "Firing trigger consent-gated?", "Consent settings", "Consent risk"],
                 [[r["tag_id"], r["tag"], r["vendor"] or r["type"], r["firing_trigger"], r["firing_event"], r["exception_events"],
                   r["exception_status"], "Yes" if r["self_gated"] else "No", "Required" if r["consent_settings"] else "Not set", r["risk"]]
                  for r in sorted(audit["consent_matrix"], key=lambda r: (r["risk"] != "No consent check", not r["exception_status"].startswith("Ineffective")))],
                 [8, 35, 18, 35, 18, 30, 26, 14, 13, 26], extra_fill=(9, {"No consent check": "F4C7C3", "OK": "DDEBC8", "Consent tool": "FCEBB6"}))
    inv = audit["inventory"]
    _table_sheet(wb, "Tag Inventory", f"{len(inv['tags'])} tags, live first. Recommendation links to the Findings Log.",
                 ["ID", "Tag name", "Type", "Status", "Firing triggers", "Exceptions", "Measurement ID", "GA4 event name",
                  "Parameters / settings", "Consent setting", "Folder", "Recommendation", "Findings"],
                 [[t["id"], t["name"], t["type"], t["status"], t["fires_on"], t["blocked_by"], t["measurement_id"], t["event_name"], t["details"],
                   t["consent"], t["folder"], t["recommendation"], t["findings"]] for t in inv["tags"]],
                 [7, 35, 20, 9, 30, 30, 18, 22, 50, 14, 14, 16, 16],
                 extra_fill=(11, {"Fix": "FAD7B5", "Delete": "E3E7ED", "Keep": "DDEBC8"}))
    _table_sheet(wb, "Trigger Inventory", f"{len(inv['triggers'])} triggers. Conditions as configured.",
                 ["ID", "Trigger name", "Type", "GTM event", "Conditions", "Used by tags", "Folder", "Recommendation", "Findings"],
                 [[t["id"], t["name"], t["type"], t["gtm_event"], t["conditions"], t["used_by"], t["folder"], t["recommendation"], t["findings"]]
                  for t in inv["triggers"]],
                 [7, 35, 18, 18, 60, 40, 14, 20, 14], extra_fill=(7, {"Fix": "FAD7B5", "Delete": "E3E7ED", "Keep": "DDEBC8"}))
    _table_sheet(wb, "Variable Inventory", f"{len(inv['variables'])} user-defined variables; built-ins enabled: {', '.join(inv['built_in_variables'])}.",
                 ["ID", "Variable", "Type", "Details", "Used by", "Folder", "Recommendation", "Findings"],
                 [[v["id"], v["name"], v["type"], v["details"], v["used_by"], v["folder"], v["recommendation"], v["findings"]] for v in inv["variables"]],
                 [7, 35, 22, 55, 40, 14, 18, 14], extra_fill=(6, {"Fix": "FAD7B5", "Delete": "E3E7ED", "Keep": "DDEBC8"}))
    legend = list(audit["legend"])
    if audit.get("expert_limitations"):
        legend.append(("Expert review limitations", audit["expert_limitations"]))
    _table_sheet(wb, "Method & Legend", "", ["Item", "Description"], [[k, v] for k, v in legend], [28, 110])
    return _bytes(wb)


def ga4_audit_xlsx(audit: dict) -> bytes:
    wb = Workbook()
    c = audit["counts"]
    ov = audit.get("overview") or {}
    eng = ov.get("engagementRate")
    _summary(wb, f"GA4 audit - {audit['property_name']}", [
        ("Property", audit["property"]), ("Run at", _when(audit["created"])), ("Data period", audit["period"]),
        ("Health score", f"{audit['score']} / 100" if audit.get("score") is not None else "Incomplete - data checks could not run"),
        ("Findings", ", ".join(f"{k}: {v}" for k, v in c.items() if v) or "none"),
        ("Property type", audit.get("property_type", "")),
        ("Sessions", ov.get("sessions")), ("Users", ov.get("totalUsers")),
        ("Engagement rate", f"{eng:.1%}" if isinstance(eng, (int, float)) else eng), ("Key events", ov.get("keyEvents")),
        ("Checks that could not run", "\n".join(audit.get("problems") or []) or "none"),
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
