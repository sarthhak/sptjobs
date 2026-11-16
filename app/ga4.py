"""GA4 Data API and Admin API helpers."""

from __future__ import annotations

from typing import Any

from .google_auth import GoogleCredentials

FILTER_OPS = {
    "equals": ("stringFilter", "EXACT"),
    "contains": ("stringFilter", "CONTAINS"),
    "begins_with": ("stringFilter", "BEGINS_WITH"),
    "ends_with": ("stringFilter", "ENDS_WITH"),
    "regex": ("stringFilter", "FULL_REGEXP"),
    "in": ("inListFilter", None),
    "eq": ("numericFilter", "EQUAL"),
    "gt": ("numericFilter", "GREATER_THAN"),
    "gte": ("numericFilter", "GREATER_THAN_OR_EQUAL"),
    "lt": ("numericFilter", "LESS_THAN"),
    "lte": ("numericFilter", "LESS_THAN_OR_EQUAL"),
}


def _filter_expr(f: dict) -> dict:
    kind, op = FILTER_OPS[f["op"]]
    if kind == "stringFilter":
        inner = {"stringFilter": {"matchType": op, "value": str(f["value"]), "caseSensitive": False}}
    elif kind == "inListFilter":
        values = f["value"] if isinstance(f["value"], list) else [f["value"]]
        inner = {"inListFilter": {"values": [str(v) for v in values], "caseSensitive": False}}
    else:
        num = float(f["value"])
        value = {"int64Value": str(int(num))} if num.is_integer() else {"doubleValue": num}
        inner = {"numericFilter": {"operation": op, "value": value}}
    expr = {"filter": {"fieldName": f["field"], **inner}}
    return {"notExpression": expr} if f.get("negate") else expr


def build_report_body(spec: dict) -> dict:
    """Turn the simplified report spec used by the agent into a GA4 runReport body."""
    metrics = spec["metrics"]
    body: dict[str, Any] = {
        "dateRanges": [{"startDate": spec.get("start_date", "28daysAgo"), "endDate": spec.get("end_date", "yesterday"), "name": "current"}],
        "dimensions": [{"name": d} for d in spec.get("dimensions", [])],
        "metrics": [{"name": m} for m in metrics],
        "limit": str(spec.get("limit", 1000)),
        "returnPropertyQuota": True,
        "keepEmptyRows": False,
    }
    if spec.get("compare_start_date") and spec.get("compare_end_date"):
        body["dateRanges"].append({"startDate": spec["compare_start_date"], "endDate": spec["compare_end_date"], "name": "previous"})
    filters = spec.get("filters") or []
    dim_f = [_filter_expr(f) for f in filters if f["field"] not in metrics]
    met_f = [_filter_expr(f) for f in filters if f["field"] in metrics]
    if dim_f:
        body["dimensionFilter"] = dim_f[0] if len(dim_f) == 1 else {"andGroup": {"expressions": dim_f}}
    if met_f:
        body["metricFilter"] = met_f[0] if len(met_f) == 1 else {"andGroup": {"expressions": met_f}}
    order = spec.get("order_by")
    if order:
        field = order["field"]
        key = {"metric": {"metricName": field}} if field in metrics else {"dimension": {"dimensionName": field}}
        body["orderBys"] = [{**key, "desc": bool(order.get("desc", True))}]
    return body


def _num(v: str) -> float | int | str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return int(f) if f.is_integer() else round(f, 6)


def flatten_report(resp: dict) -> dict:
    dims = [h["name"] for h in resp.get("dimensionHeaders", [])]
    mets = [h["name"] for h in resp.get("metricHeaders", [])]
    rows = []
    for r in resp.get("rows", []):
        row = {d: v.get("value") for d, v in zip(dims, r.get("dimensionValues", []))}
        row.update({m: _num(v.get("value")) for m, v in zip(mets, r.get("metricValues", []))})
        rows.append(row)
    meta = resp.get("metadata", {})
    warnings = []
    if meta.get("subjectToThresholding"):
        warnings.append("Data is subject to thresholding (low counts hidden, usually because Google signals is on).")
    if meta.get("dataLossFromOtherRow"):
        warnings.append("Some rows were grouped into '(other)' because of high cardinality.")
    if meta.get("samplingMetadatas"):
        warnings.append("Results are sampled.")
    quota = resp.get("propertyQuota", {}).get("tokensPerDay", {})
    return {
        "columns": dims + mets,
        "dimensions": dims,
        "metrics": mets,
        "rows": rows,
        "row_count": int(resp.get("rowCount", len(rows))),
        "currency": meta.get("currencyCode"),
        "time_zone": meta.get("timeZone"),
        "warnings": warnings,
        "quota_tokens_remaining_today": quota.get("remaining"),
    }


class GA4Client:
    def __init__(self, creds: GoogleCredentials):
        self._creds = creds

    def _data(self):
        return self._creds.build("analyticsdata", "v1beta")

    def _admin(self, version: str = "v1beta"):
        return self._creds.build("analyticsadmin", version)

    def list_properties(self) -> list[dict]:
        admin = self._admin()
        out, token = [], None
        while True:
            resp = admin.accountSummaries().list(pageSize=200, pageToken=token).execute(num_retries=3)
            for acc in resp.get("accountSummaries", []):
                for p in acc.get("propertySummaries", []):
                    out.append({
                        "account": acc.get("displayName"),
                        "property": p["property"],
                        "property_name": p.get("displayName"),
                    })
            token = resp.get("nextPageToken")
            if not token:
                return out

    def metadata(self, prop: str) -> dict:
        resp = self._data().properties().getMetadata(name=f"{prop}/metadata").execute(num_retries=3)
        return {
            "dimensions": [{"api_name": d["apiName"], "ui_name": d.get("uiName"), "custom": d.get("customDefinition", False)} for d in resp.get("dimensions", [])],
            "metrics": [{"api_name": m["apiName"], "ui_name": m.get("uiName"), "type": m.get("type"), "custom": m.get("customDefinition", False)} for m in resp.get("metrics", [])],
        }

    def run_report(self, prop: str, spec: dict) -> dict:
        body = build_report_body(spec)
        resp = self._data().properties().runReport(property=prop, body=body).execute(num_retries=3)
        return flatten_report(resp)

    def run_realtime(self, prop: str, dimensions: list[str], metrics: list[str], limit: int = 50) -> dict:
        body = {"dimensions": [{"name": d} for d in dimensions], "metrics": [{"name": m} for m in metrics], "limit": str(limit)}
        resp = self._data().properties().runRealtimeReport(property=prop, body=body).execute(num_retries=3)
        return flatten_report(resp)

    def property_config(self, prop: str) -> dict:
        """Collects property settings. Each part is best-effort so one failure doesn't hide the rest."""
        admin = self._admin()
        out: dict[str, Any] = {"errors": {}}

        def grab(key, fn):
            try:
                out[key] = fn()
            except Exception as exc:  # noqa: BLE001 - report and continue
                out["errors"][key] = str(exc)[:300]

        props = admin.properties()
        grab("property", lambda: props.get(name=prop).execute(num_retries=3))
        grab("data_retention", lambda: props.getDataRetentionSettings(name=f"{prop}/dataRetentionSettings").execute(num_retries=3))
        grab("data_streams", lambda: props.dataStreams().list(parent=prop).execute(num_retries=3).get("dataStreams", []))
        grab("key_events", lambda: props.keyEvents().list(parent=prop).execute(num_retries=3).get("keyEvents", []))
        grab("custom_dimensions", lambda: props.customDimensions().list(parent=prop).execute(num_retries=3).get("customDimensions", []))
        grab("custom_metrics", lambda: props.customMetrics().list(parent=prop).execute(num_retries=3).get("customMetrics", []))
        grab("google_ads_links", lambda: props.googleAdsLinks().list(parent=prop).execute(num_retries=3).get("googleAdsLinks", []))

        def enhanced():
            alpha = self._admin("v1alpha").properties().dataStreams()
            res = {}
            for s in out.get("data_streams", []):
                if s.get("type") == "WEB_DATA_STREAM":
                    res[s["name"]] = alpha.getEnhancedMeasurementSettings(name=f"{s['name']}/enhancedMeasurementSettings").execute(num_retries=3)
            return res

        grab("enhanced_measurement", enhanced)
        out["checks"] = check_property_config(out)
        return out


def check_property_config(cfg: dict) -> list[dict]:
    checks = []

    def add(severity, title, detail):
        checks.append({"severity": severity, "title": title, "detail": detail})

    retention = (cfg.get("data_retention") or {}).get("eventDataRetention")
    if retention == "TWO_MONTHS":
        add("high", "Event data retention is 2 months", "Explorations can only look back 2 months. Set it to 14 months (Admin > Data retention).")
    if "key_events" in cfg and not cfg["key_events"]:
        add("high", "No key events", "No key events (conversions) are marked, so conversion reporting and ad bidding have nothing to optimise for.")
    streams = cfg.get("data_streams")
    if streams is not None and not any(s.get("type") == "WEB_DATA_STREAM" for s in streams):
        add("medium", "No web data stream", "The property has no web data stream.")
    for name, em in (cfg.get("enhanced_measurement") or {}).items():
        if not em.get("streamEnabled"):
            add("medium", "Enhanced measurement is off", f"Enhanced measurement is disabled on {name}.")
    if "custom_dimensions" in cfg and not cfg["custom_dimensions"]:
        add("low", "No custom dimensions", "Event parameters you send (beyond the automatic ones) are not reportable until registered as custom dimensions.")
    prop = cfg.get("property") or {}
    if prop and not prop.get("currencyCode"):
        add("low", "No reporting currency", "Set a currency so revenue is reported consistently.")
    if "google_ads_links" in cfg and not cfg["google_ads_links"]:
        add("info", "No Google Ads link", "Link Google Ads if you run Ads, so conversions and audiences flow across.")
    return checks
