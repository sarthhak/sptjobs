"""Optional AI 'expert review' of a GTM audit: Claude reads a compact description of the container and the rule-based
findings, then adds what needs judgment - executive summary, priorities, extra findings with evidence, and an event model."""

from __future__ import annotations

import json

from ..usage import record
from .meta import AREAS
from .report import build_gtm_report

FALLBACK_BETA = "server-side-fallback-2026-07-01"

_SEV = ["critical", "high", "medium", "low"]
_STR = {"type": "string"}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SCHEMA = _obj({
    "executive_summary": _STR,
    "top_priorities": {"type": "array", "items": _obj({"title": _STR, "detail": _STR, "finding_refs": {"type": "array", "items": _STR}})},
    "additional_findings": {"type": "array", "items": _obj({
        "area": {"type": "string", "enum": AREAS}, "severity": {"type": "string", "enum": _SEV},
        "effort": {"type": "string", "enum": ["S", "M", "L"]}, "title": _STR, "detail": _STR, "evidence": _STR, "impact": _STR,
        "fix": _STR, "validate": _STR, "owner": _STR})},
    "event_model": {"type": "array", "items": _obj({"current_events": _STR, "proposed_event": _STR, "parameters": _STR,
                                                     "key_event": _STR, "notes": _STR})},
    "custom_dimensions": {"type": "array", "items": _STR},
    "limitations": _STR,
})

SYSTEM = """\
You are a senior digital analytics implementation consultant reviewing a Google Tag Manager container for the
owner of the site. A rule engine has already produced findings (refs F-xx). Your job is the judgment layer:

1. executive_summary: 4-6 sentences for a marketing/analytics lead. What state is the container in, what are the
   biggest risks (consent, data quality, missing measurement), and what to do first. Plain language.
2. top_priorities: exactly 5, ordered. Each is an action ("Stop consent leaks this week") with a short detail that
   names the concrete steps and the finding refs it covers (rule refs or your new findings' titles).
3. additional_findings: problems the rules did NOT already report, that you can evidence from the container data
   given (cite tag/trigger/variable IDs and names). Look especially for: duplicate or fragmented tracking of the
   same user action, missing lead/conversion measurement, media pixels sending only page views, market/property
   routing logic that is brittle, consent logic gaps, tags whose purpose conflicts with their trigger, vendor
   overlap (e.g. two chat tools), and anything that would break during a site migration (selectors on CSS classes,
   click text). Do not repeat rule findings. Mark anything you can't confirm from the data with "[verify]".
   Keep evidence specific; keep each field to 1-3 sentences.
4. event_model: a proposed GA4 event model that replaces the current live GA4 event tags: snake_case
   verb_object names, Google's recommended events where they fit, few events with parameters instead of one event
   per CTA. current_events lists the live event names each row replaces (or "(none - gap)").
5. custom_dimensions: event-scoped parameters from your model that should be registered in GA4.
6. limitations: one or two sentences on what this static review cannot see.
"""


def _compact(report: dict) -> str:
    inv = report["inventory"]
    lines = ["# At a glance"] + [f"- {k}: {v}" for k, v in report["glance"]]
    lines.append("\n# Rule findings")
    for f in report["findings"]:
        if f["source"] == "Rules":
            lines.append(f"- {f['ref']} [{f['severity']}/{f['area']}] {f['title']}. Evidence: {f['evidence'][:600]}")
    lines.append("\n# Tags (id | name | type | status | fires on | blocked by | consent | details)")
    for t in inv["tags"]:
        lines.append(f"- {t['id']} | {t['name']} | {t['type']} | {t['status']} | {'; '.join(t['fires_on']) or '-'} | "
                     f"{'; '.join(t['blocked_by']) or '-'} | {t['consent']} | {t['details'][:300]}")
    lines.append("\n# Triggers (id | name | type | GTM event | conditions | used by)")
    for t in inv["triggers"]:
        lines.append(f"- {t['id']} | {t['name']} | {t['type']} | {t['gtm_event']} | {t['conditions'][:300]} | {'; '.join(t['used_by'][:6]) or '-'}")
    lines.append("\n# Variables (id | name | type | details | used by)")
    for v in inv["variables"]:
        lines.append(f"- {v['id']} | {v['name']} | {v['type']} | {v['details'][:200]} | {'; '.join(v['used_by'][:6]) or '-'}")
    lines.append("\n# Consent matrix (live tags x firing triggers)")
    for r in report["consent_matrix"]:
        lines.append(f"- tag {r['tag_id']} {r['tag']} ({r['vendor'] or r['type']}) on '{r['firing_trigger']}' [{r['firing_event']}] "
                     f"exceptions {r['exception_events']} -> {r['exception_status']}; risk: {r['risk']}")
    return "\n".join(lines)


async def run_expert_review(ctx, container, audit_result: dict) -> dict:
    report = build_gtm_report(container, audit_result)
    async with ctx.anthropic.beta.messages.stream(
        model=ctx.settings.model,
        max_tokens=32000,
        system=SYSTEM,
        messages=[{"role": "user", "content": _compact(report)}],
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    ) as stream:
        message = await stream.get_final_message()
    if message.usage:
        record(ctx.store, "expert_review", message.model or ctx.settings.model, message.usage)
    if message.stop_reason == "refusal":
        raise RuntimeError("Claude declined to review this container.")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("The review was cut off by the output limit; try again.")
    text = next((b.text for b in message.content if b.type == "text"), "")
    return json.loads(text)
