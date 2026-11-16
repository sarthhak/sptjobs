"""Builds chart specs (rendered by Chart.js in the browser) from stored GA4 results."""

from __future__ import annotations

from collections import OrderedDict

TIME_DIMS = {"date", "dateHour", "dateHourMinute", "yearMonth", "yearWeek", "isoYearIsoWeek", "year", "month", "week", "day", "hour", "nthDay"}


def _label(dim: str, value) -> str:
    v = str(value)
    if dim == "date" and len(v) == 8 and v.isdigit():
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    if dim == "yearMonth" and len(v) == 6 and v.isdigit():
        return f"{v[:4]}-{v[4:]}"
    return v


def build_chart(result: dict, chart_type: str, title: str, x: str, y: list[str], max_points: int = 50) -> dict:
    rows = result.get("rows", [])
    cols = result.get("columns", [])
    for field in [x, *y]:
        if field not in cols:
            raise ValueError(f"'{field}' is not a column of this result; columns are {cols}")
    if chart_type == "table":
        return {"chart_type": "table", "title": title, "columns": [x, *y], "rows": [[r.get(c) for c in [x, *y]] for r in rows[:max_points]]}

    has_ranges = "dateRange" in cols and x != "dateRange"
    if x in TIME_DIMS:
        rows = sorted(rows, key=lambda r: str(r.get(x)))
    labels: "OrderedDict[str, None]" = OrderedDict()
    series: dict[str, dict[str, float]] = OrderedDict()
    for r in rows:
        label = _label(x, r.get(x))
        if label not in labels and len(labels) >= max_points:
            continue
        labels[label] = None
        for metric in y:
            name = f"{metric} ({r['dateRange']})" if has_ranges else metric
            series.setdefault(name, {})
            series[name][label] = series[name].get(label, 0) + (r.get(metric) or 0)
    lab = list(labels)
    return {
        "chart_type": chart_type,
        "title": title,
        "labels": lab,
        "datasets": [{"label": name, "data": [vals.get(l, 0) for l in lab]} for name, vals in series.items()],
    }
