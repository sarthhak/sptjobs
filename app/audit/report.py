"""Builds the consultant-style GTM audit report: at-a-glance, scorecard by area, priorities, findings log with
priority/phase/effort/validation, action plan, consent matrix and inventories with a recommendation per item."""

from __future__ import annotations

from collections import defaultdict

from .consent import consent_matrix, consent_mode_status, template_names, third_party_live, trigger_event, event_label
from .container import Container, param, var_refs
from .describe import enrich_findings, inventory
from .meta import AREAS, LEGEND, health, meta, phase, priority
from .rules import SEVERITY_ORDER

SEV_RANK = {s: i for i, s in enumerate(SEVERITY_ORDER)}


def _measurement_ids(c: Container) -> list[str]:
    ids = set()
    for t in c.tags:
        for key in ("tagId", "measurementIdOverride"):
            v = param(t, key)
            if isinstance(v, str) and v.startswith("G-"):
                ids.add(v)
    for v in c.variables:
        for row in param(v, "map", []) or []:
            for p in row.get("map", []) or []:
                if str(p.get("value", "")).startswith("G-"):
                    ids.add(p["value"])
    return sorted(ids)


def at_a_glance(c: Container) -> list[tuple[str, str]]:
    live = [t for t in c.tags if not t.get("paused")]
    used = c.referenced_trigger_ids()
    live_used = {i for t in live for i in (t.get("firingTriggerId") or []) + (t.get("blockingTriggerId") or [])}
    only_paused = [t for t in c.triggers if t["triggerId"] in used and t["triggerId"] not in live_used]
    refs = set()
    for e in c.tags + c.triggers + c.variables:
        refs |= var_refs(e)
    builtins_referenced = len([n for n in c.builtin_names if n in refs])
    templates = template_names(c)
    tmpl = []
    for t in c.raw.get("customTemplate", []) or []:
        users = [tag for tag in c.tags if templates.get(tag["type"]) == t.get("name")]
        state = "unused" if not users else ("live" if any(not u.get("paused") for u in users) else "paused")
        tmpl.append(f"{t.get('name')} - {state}")
    cm = consent_mode_status(c)
    return [
        ("Container", f"{c.name} ({c.container.get('publicId')})"),
        ("Audited version", f"Live version {c.version_id}"),
        ("GA4 measurement IDs", ", ".join(_measurement_ids(c)) or "none found"),
        ("Tags (live / paused)", f"{len(c.tags)} ({len(live)} live / {len(c.tags) - len(live)} paused)"),
        ("Triggers (unused / only paused tags)", f"{len(c.triggers)} ({len([t for t in c.triggers if t['triggerId'] not in used])} unused / {len(only_paused)} only paused)"),
        ("User-defined variables / built-ins enabled", f"{len(c.variables)} / {len(c.builtin_names)} ({builtins_referenced} referenced)"),
        ("Custom templates", " · ".join(tmpl) or "none"),
        ("Third-party tags live", ", ".join(third_party_live(c)) or "none"),
        ("Consent Mode", ("set by " + ", ".join(cm["live"])) if cm["live"] else ("not live" + (f" (paused: {', '.join(cm['paused'])})" if cm["paused"] else ""))),
        ("Folders", str(len(c.folders))),
    ]


def _evidence(f: dict) -> str:
    notes = f.get("item_notes") or {}
    lines = []
    for a in f.get("affected", []):
        note = notes.get(a["id"], "")
        lines.append(f"{a['kind']} {a['id']} '{a['name']}'" + (f": {note}" if note and len(f.get("affected", [])) > 1 else ""))
    return "\n".join(lines) or f.get("detail", "")


def build_findings(c: Container, raw_findings: list[dict], expert: dict | None) -> list[dict]:
    enriched = enrich_findings(c, raw_findings)
    rows = []
    for f in enriched:
        m = meta(f["rule"])
        rows.append({
            "finding_id": f["id"], "rule": f["rule"], "source": "Rules", "area": m["area"], "severity": f["severity"],
            "effort": m["effort"], "title": f["title"],
            "detail": f["detail"] if f["detail"] != m["what"] or not m["what"] else m["what"],
            "evidence": _evidence(f), "impact": m["impact"] or f.get("impact", ""), "fix": f["recommendation"],
            "validate": m["validate"], "owner": m["owner"], "fixable": f.get("fixable", False),
            "item_fixable": f.get("item_fixable") or {}, "item_notes": f.get("item_notes") or {}, "affected": f.get("affected", []),
        })
    for x in (expert or {}).get("additional_findings", []):
        rows.append({"finding_id": None, "rule": "EXPERT", "source": "Expert review", "area": x["area"], "severity": x["severity"],
                     "effort": x["effort"], "title": x["title"], "detail": x["detail"], "evidence": x["evidence"], "impact": x["impact"],
                     "fix": x["fix"], "validate": x["validate"], "owner": x["owner"], "fixable": False, "item_fixable": {},
                     "item_notes": {}, "affected": []})
    rows.sort(key=lambda r: (SEV_RANK[r["severity"]], r["effort"] != "S", AREAS.index(r["area"]) if r["area"] in AREAS else 99))
    for i, r in enumerate(rows, 1):
        r["ref"] = f"F-{i:02d}"
        r["priority"] = priority(r["severity"], r["effort"])
        r["phase"] = phase(r["severity"], r["effort"])
        r["status"] = "Open"
    return rows


def scorecard(findings: list[dict]) -> list[dict]:
    out = []
    for area in AREAS:
        counts = {s: sum(1 for f in findings if f["area"] == area and f["severity"] == s) for s in SEVERITY_ORDER}
        out.append({"area": area, **counts, "total": sum(counts.values()), "health": health(counts)})
    totals = {s: sum(r[s] for r in out) for s in SEVERITY_ORDER}
    out.append({"area": "Total", **totals, "total": sum(totals.values()), "health": health(totals)})
    return out


def top_priorities(findings: list[dict], expert: dict | None) -> list[dict]:
    if expert and expert.get("top_priorities"):
        return expert["top_priorities"]
    top = [f for f in findings if f["severity"] in ("critical", "high")][:5] or findings[:5]
    return [{"title": f["title"], "detail": f["fix"], "finding_refs": [f["ref"]]} for f in top]


def action_plan(findings: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for f in findings:
        if f["severity"] != "info":
            groups[(f["phase"], f["area"])].append(f)
    rows = []
    for (ph, area), fs in sorted(groups.items(), key=lambda kv: (kv[0][0], AREAS.index(kv[0][1]) if kv[0][1] in AREAS else 99)):
        owners = sorted({o.strip() for f in fs for o in f["owner"].split("+")})
        rows.append({"phase": ph, "workstream": area, "actions": [f"{f['ref']}: {f['fix']}" for f in fs],
                     "findings": ", ".join(f["ref"] for f in fs), "owner": " + ".join(owners),
                     "done_when": "; ".join(dict.fromkeys(f["validate"] for f in fs if f["validate"] and f["validate"] != "-"))})
    return rows


def _refs_by_entity(findings: list[dict]) -> dict[tuple, list[str]]:
    refs: dict[tuple, list[str]] = defaultdict(list)
    for f in findings:
        for a in f["affected"]:
            refs[(a["kind"].lower(), a["id"])].append(f["ref"])
    return refs


def inventories(c: Container, findings: list[dict]) -> dict:
    inv = inventory(c, [])
    refs = _refs_by_entity(findings)
    paused_only = {f["rule"]: set(a["id"] for a in f["affected"]) for f in findings}
    for t in inv["tags"]:
        r = refs.get(("tag", t["id"]), [])
        t["findings"] = ", ".join(r)
        t["recommendation"] = "Delete (paused)" if t["status"] == "Paused" else ("Fix" if r else "Keep")
        tag = c.tag_by_id[t["id"]]
        t["measurement_id"] = param(tag, "measurementIdOverride") or (param(tag, "tagId") if tag["type"] == "googtag" else "") or ""
        t["event_name"] = param(tag, "eventName") or ""
    for t in inv["triggers"]:
        r = refs.get(("trigger", t["id"]), [])
        t["findings"] = ", ".join(r)
        t["gtm_event"] = event_label(*trigger_event(c, t["id"]))
        if t["id"] in paused_only.get("UNUSED_TRIGGER", set()):
            t["recommendation"] = "Delete (unused)"
        elif t["id"] in paused_only.get("TRIGGERS_ONLY_PAUSED", set()):
            t["recommendation"] = "Delete with paused tags"
        else:
            t["recommendation"] = "Fix" if r else "Keep"
    for v in inv["variables"]:
        r = refs.get(("variable", v["id"]), [])
        v["findings"] = ", ".join(r)
        v["recommendation"] = "Delete (unused)" if v["id"] in paused_only.get("UNUSED_VARIABLE", set()) else ("Fix" if r else "Keep")
    live_first = lambda t: (t.get("status") == "Paused", t["name"].lower())  # noqa: E731
    inv["tags"].sort(key=live_first)
    return inv


def build_gtm_report(c: Container, audit_result: dict, expert: dict | None = None) -> dict:
    findings = build_findings(c, audit_result["findings"], expert)
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITY_ORDER}
    return {
        "glance": at_a_glance(c),
        "scorecard": scorecard(findings),
        "health": health(counts),
        "counts": counts,
        "top_priorities": top_priorities(findings, expert),
        "executive_summary": (expert or {}).get("executive_summary", ""),
        "findings": findings,
        "action_plan": action_plan(findings),
        "consent_matrix": consent_matrix(c),
        "inventory": inventories(c, findings),
        "event_model": (expert or {}).get("event_model", []),
        "custom_dimensions": (expert or {}).get("custom_dimensions", []),
        "expert_limitations": (expert or {}).get("limitations", ""),
        "has_expert_review": bool(expert),
        "legend": LEGEND,
    }
