"""Tools the assistant can call. Every input is validated before it runs.

The assistant can read GA4 and GTM, run audits and *propose* GTM change sets. It cannot apply or publish:
those steps only exist as buttons in the web UI, behind a person's approval.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..audit.container import Container
from ..audit.fixes import build_changeset
from ..audit.rules import run_audit
from ..charts import build_chart
from ..context import AppContext

PROPERTY = r"^properties/\d+$"
CONTAINER = r"^accounts/\d+/containers/\d+$"
MAX_ROWS_TO_MODEL = 100


class _M(BaseModel):
    model_config = {"extra": "forbid"}


class Empty(_M):
    pass


class PropertyIn(_M):
    property: str = Field(pattern=PROPERTY, description="GA4 property resource name, e.g. properties/123456789")


class FilterIn(_M):
    field: str = Field(description="Dimension or metric API name")
    op: Literal["equals", "contains", "begins_with", "ends_with", "regex", "in", "eq", "gt", "gte", "lt", "lte"]
    value: Any = Field(description="String, number, or list of strings for 'in'")
    negate: bool = False


class OrderIn(_M):
    field: str
    desc: bool = True


class ReportSpec(_M):
    start_date: str = Field("28daysAgo", description="YYYY-MM-DD, 'today', 'yesterday' or 'NdaysAgo'")
    end_date: str = Field("yesterday")
    dimensions: list[str] = Field(default_factory=list, max_length=9, description="GA4 dimension API names, e.g. date, sessionDefaultChannelGroup, pagePath, eventName")
    metrics: list[str] = Field(min_length=1, max_length=10, description="GA4 metric API names, e.g. sessions, activeUsers, keyEvents, totalRevenue, engagementRate")
    filters: list[FilterIn] = Field(default_factory=list, description="All filters are combined with AND")
    order_by: Optional[OrderIn] = None
    limit: int = Field(1000, ge=1, le=10000)
    compare_start_date: Optional[str] = Field(None, description="Optional comparison range; adds a 'dateRange' column")
    compare_end_date: Optional[str] = None


class RunReportIn(ReportSpec):
    property: str = Field(pattern=PROPERTY)


class RealtimeIn(_M):
    property: str = Field(pattern=PROPERTY)
    dimensions: list[str] = Field(default_factory=list, max_length=4, description="e.g. unifiedScreenName, eventName, country, deviceCategory")
    metrics: list[str] = Field(default_factory=lambda: ["activeUsers"], min_length=1, max_length=5)
    limit: int = Field(50, ge=1, le=250)


ChartType = Literal["line", "bar", "horizontal_bar", "pie", "doughnut", "table"]


class ChartIn(_M):
    result_id: int = Field(description="result_id returned by ga4_run_report")
    chart_type: ChartType
    title: str
    x: str = Field(description="Column for the x axis / slice labels (usually a dimension)")
    y: list[str] = Field(min_length=1, max_length=6, description="Metric columns to plot")
    max_points: int = Field(50, ge=2, le=500)


class WidgetIn(_M):
    title: str
    chart_type: ChartType
    report: ReportSpec = Field(description="Use relative dates (e.g. 28daysAgo..yesterday) so the dashboard stays current")
    x: str
    y: list[str] = Field(min_length=1, max_length=6)


class DashboardIn(_M):
    name: str
    description: str = ""
    property: str = Field(pattern=PROPERTY)
    widgets: list[WidgetIn] = Field(min_length=1, max_length=20)


class ReportIn(_M):
    title: str
    markdown: str = Field(description="Full report in Markdown. Embed charts you rendered with [[chart:ID]] on their own line.")


class ContainerIn(_M):
    container_path: str = Field(pattern=CONTAINER, description="e.g. accounts/123/containers/456")


class EntityIn(ContainerIn):
    kind: Literal["tag", "trigger", "variable"]
    entity_id: str


class CustomOp(_M):
    op: Literal["create", "update", "delete"]
    kind: Literal["tag", "trigger", "variable"]
    entity_id: Optional[str] = Field(None, description="Required for update and delete")
    patch: Optional[dict] = Field(None, description="update: top-level fields to replace, e.g. {\"paused\": true} or a full new 'parameter' list")
    body: Optional[dict] = Field(None, description="create: the full GTM API resource (name, type, parameter, firingTriggerId, ...)")
    reason: str


class ProposeIn(ContainerIn):
    audit_id: int = Field(description="Audit the change set is based on; run gtm_run_audit first")
    summary: str = Field(description="One or two sentences describing the change set for the reviewer")
    finding_ids: list[str] = Field(default_factory=list, description="Finding ids whose automatic fix should be included")
    custom_ops: list[CustomOp] = Field(default_factory=list, description="Extra hand-written operations")


class VerifyIn(_M):
    url: str
    wait_seconds: int = Field(4, ge=1, le=15)
    click_selector: Optional[str] = Field(None, description="Optional CSS selector to click after load, to test click tracking")

    @field_validator("url")
    @classmethod
    def _http(cls, v: str) -> str:
        if not v.startswith(("https://", "http://")):
            raise ValueError("url must start with http:// or https://")
        return v


@dataclass
class Tool:
    name: str
    description: str
    model: type[_M]


TOOLS = [
    Tool("ga4_list_properties", "List the GA4 properties these credentials can read.", Empty),
    Tool("ga4_get_metadata", "List dimensions and metrics (including custom definitions) available in a GA4 property. Use it before a report if you're unsure of an API name.", PropertyIn),
    Tool("ga4_run_report", "Run a GA4 report. Returns up to 100 rows plus a result_id for charts; warnings flag thresholding, sampling and '(other)' rows.", RunReportIn),
    Tool("ga4_run_realtime_report", "Realtime report for the last 30 minutes. Useful to confirm a tag change is sending data.", RealtimeIn),
    Tool("ga4_audit_property", "Read a GA4 property's configuration (retention, streams, key events, custom definitions, enhanced measurement, Ads links) and check it against best practice.", PropertyIn),
    Tool("render_chart", "Show a chart of a report result to the user, inline in the chat. Returns a chart id that reports can embed.", ChartIn),
    Tool("save_dashboard", "Save a dashboard of live GA4 widgets. Each widget re-runs its report whenever the dashboard is opened.", DashboardIn),
    Tool("save_report", "Save a written report (Markdown, may embed charts) that the user can open, print or save as PDF.", ReportIn),
    Tool("gtm_list_containers", "List the GTM containers these credentials can access, with their container_path.", Empty),
    Tool("gtm_container_overview", "List the tags, triggers and variables in the live (published) version of a container.", ContainerIn),
    Tool("gtm_get_entity", "Full JSON of one tag, trigger or variable from the live version.", EntityIn),
    Tool("gtm_run_audit", "Audit the live version of a container against implementation best practice. Returns a health score and findings; findings with fixable=true have an automatic fix.", ContainerIn),
    Tool("gtm_propose_changes", "Create a change set for the user to review. Nothing is changed in GTM: the user reviews the diff in the Changes tab and decides whether to apply and publish it.", ProposeIn),
    Tool("verify_site", "Load a page in a headless browser and report which GTM containers load, which GA4 events fire (intercepted, not sent to GA4) and the dataLayer contents.", VerifyIn),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def _inline_refs(schema: dict) -> dict:
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def tool_definitions() -> list[dict]:
    return [{
        "name": t.name,
        "description": t.description,
        "input_schema": _inline_refs(t.model.model_json_schema()),
        "eager_input_streaming": True,
    } for t in TOOLS]


@dataclass
class ToolOutcome:
    content: str
    is_error: bool = False
    events: list[dict] = field(default_factory=list)


def _json(obj: Any) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


def strip_snapshot(audit_result: dict) -> dict:
    return {k: v for k, v in audit_result.items() if k != "version_snapshot"}


async def run_gtm_audit(ctx: AppContext, container_path: str) -> tuple[int, dict]:
    version = await asyncio.to_thread(ctx.gtm.get_live_version, container_path)
    result = run_audit(Container(version), ctx.settings.tag_naming_pattern)
    result["version_snapshot"] = version
    audit_id = ctx.store.save_audit(container_path, result)
    return audit_id, result


async def execute(ctx: AppContext, conversation_id: int | None, name: str, raw: Any) -> ToolOutcome:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return ToolOutcome(f"Unknown tool {name}", True)
    if not isinstance(raw, dict):
        return ToolOutcome(_json({"INVALID_JSON": _json(raw)}), True)
    try:
        args = tool.model.model_validate(raw)
    except ValidationError as e:
        return ToolOutcome(f"Invalid input: {e.errors(include_url=False)}", True)
    try:
        return await _HANDLERS[name](ctx, conversation_id, args)
    except Exception as e:  # noqa: BLE001 - tool errors go back to the model as text
        return ToolOutcome(f"{type(e).__name__}: {str(e)[:1500]}", True)


# --- handlers -----------------------------------------------------------------

async def _list_properties(ctx, cid, a):
    return ToolOutcome(_json(await asyncio.to_thread(ctx.ga4.list_properties)))


async def _metadata(ctx, cid, a: PropertyIn):
    return ToolOutcome(_json(await asyncio.to_thread(ctx.ga4.metadata, a.property)))


async def _run_report(ctx, cid, a: RunReportIn):
    spec = a.model_dump(exclude_none=True)
    data = await asyncio.to_thread(ctx.ga4.run_report, a.property, spec)
    rid = ctx.store.save_result(cid, spec, data)
    shown = data["rows"][:MAX_ROWS_TO_MODEL]
    out = {k: v for k, v in data.items() if k != "rows"}
    out.update(result_id=rid, rows=shown)
    if len(data["rows"]) > len(shown):
        out["note"] = f"Showing {len(shown)} of {len(data['rows'])} rows; all rows are stored under result_id {rid} for charts."
    return ToolOutcome(_json(out))


async def _realtime(ctx, cid, a: RealtimeIn):
    data = await asyncio.to_thread(ctx.ga4.run_realtime, a.property, a.dimensions, a.metrics, a.limit)
    rid = ctx.store.save_result(cid, a.model_dump(), data)
    return ToolOutcome(_json({**data, "result_id": rid}))


async def _audit_property(ctx, cid, a: PropertyIn):
    return ToolOutcome(_json(await asyncio.to_thread(ctx.ga4.property_config, a.property)))


async def _render_chart(ctx, cid, a: ChartIn):
    result = ctx.store.get_result(a.result_id)
    if not result:
        return ToolOutcome(f"No result with id {a.result_id}", True)
    spec = build_chart(result["data"], a.chart_type, a.title, a.x, a.y, a.max_points)
    chart_id = ctx.store.save_chart(cid, spec)
    return ToolOutcome(_json({"chart_id": chart_id, "shown_to_user": True}), events=[{"type": "chart", "id": chart_id, "spec": spec}])


async def _save_dashboard(ctx, cid, a: DashboardIn):
    widgets = [w.model_dump(exclude_none=True) for w in a.widgets]
    did = ctx.store.save_dashboard(a.name, a.description, a.property, widgets)
    return ToolOutcome(_json({"dashboard_id": did, "url": f"/#dashboards/{did}"}), events=[{"type": "dashboard", "id": did, "name": a.name}])


async def _save_report(ctx, cid, a: ReportIn):
    rid = ctx.store.save_report(cid, a.title, a.markdown)
    return ToolOutcome(_json({"report_id": rid, "url": f"/reports/{rid}"}), events=[{"type": "report", "id": rid, "title": a.title}])


async def _list_containers(ctx, cid, a):
    return ToolOutcome(_json(await asyncio.to_thread(ctx.gtm.list_containers)))


async def _overview(ctx, cid, a: ContainerIn):
    version = await asyncio.to_thread(ctx.gtm.get_live_version, a.container_path)
    return ToolOutcome(_json(Container(version).summary()))


async def _get_entity(ctx, cid, a: EntityIn):
    version = await asyncio.to_thread(ctx.gtm.get_live_version, a.container_path)
    ent = Container(version).entity(a.kind, a.entity_id)
    if ent is None:
        return ToolOutcome(f"No {a.kind} with id {a.entity_id} in the live version", True)
    return ToolOutcome(_json(ent))


async def _run_audit(ctx, cid, a: ContainerIn):
    audit_id, result = await run_gtm_audit(ctx, a.container_path)
    out = strip_snapshot(result)
    out["audit_id"] = audit_id
    return ToolOutcome(_json(out), events=[{"type": "audit", "id": audit_id, "score": result["score"], "container": result["container"]}])


async def _propose(ctx, cid, a: ProposeIn):
    audit = ctx.store.get_audit(a.audit_id)
    if not audit or audit["container_path"] != a.container_path:
        return ToolOutcome(f"Audit {a.audit_id} not found for {a.container_path}", True)
    result = audit["result"]
    c = Container(result["version_snapshot"])
    ops, errors = build_changeset(c, result["findings"], a.finding_ids, [o.model_dump(exclude_none=True) for o in a.custom_ops])
    if not ops:
        return ToolOutcome(_json({"error": "No operations could be built", "details": errors}), True)
    csid = ctx.store.create_changeset(container_path=a.container_path, audit_id=a.audit_id, version_id=result.get("version_id"),
                                      summary=a.summary, ops=ops, errors=errors)
    brief = [{"op": o["op"], "kind": o["kind"], "name": o["name"], "reason": o["reason"]} for o in ops]
    return ToolOutcome(
        _json({"changeset_id": csid, "status": "proposed - waiting for the user's review in the Changes tab", "operations": brief, "skipped": errors}),
        events=[{"type": "changeset", "id": csid, "summary": a.summary, "count": len(ops)}],
    )


async def _verify(ctx, cid, a: VerifyIn):
    return ToolOutcome(_json(await ctx.verify_site(a.url, a.wait_seconds * 1000, a.click_selector)))


_HANDLERS = {
    "ga4_list_properties": _list_properties,
    "ga4_get_metadata": _metadata,
    "ga4_run_report": _run_report,
    "ga4_run_realtime_report": _realtime,
    "ga4_audit_property": _audit_property,
    "render_chart": _render_chart,
    "save_dashboard": _save_dashboard,
    "save_report": _save_report,
    "gtm_list_containers": _list_containers,
    "gtm_container_overview": _overview,
    "gtm_get_entity": _get_entity,
    "gtm_run_audit": _run_audit,
    "gtm_propose_changes": _propose,
    "verify_site": _verify,
}
