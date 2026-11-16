"""What each audit rule means for a reader: area, generic description, impact, effort, owner and how to validate.
Also the priority/phase/health formulas used in reports."""

from __future__ import annotations

AREAS = ["Consent & privacy", "GA4 data quality", "Measurement coverage", "Event taxonomy", "Tag governance", "Martech stack"]

# rule: (area, what's happening, why it matters, effort S/M/L, owner, how to validate)
RULE_META: dict[str, tuple[str, str, str, str, str, str]] = {
    "GA4_NO_MEASUREMENT_ID": ("Measurement coverage", "GA4 event tags with no measurement ID have nowhere to send data.",
        "Events from these tags are not collected at all.", "S", "GTM owner",
        "Preview: the tag fires and the GA4 request (collect?v=2) carries the right tid."),
    "BROKEN_TRIGGER_REF": ("Measurement coverage", "Tags point at trigger IDs that no longer exist.",
        "The tag can't fire on the trigger that was meant to fire it.", "S", "GTM owner",
        "Preview: perform the action and confirm the tag fires."),
    "BROKEN_VARIABLE_REF": ("GA4 data quality", "Items reference variables that are neither user-defined nor enabled built-ins, so they resolve to undefined.",
        "Values are sent as 'undefined'; reports show blanks or (not set).", "S", "GTM owner",
        "Preview > Variables: the variable resolves to a real value."),
    "DUPLICATE_TAG": ("GA4 data quality", "Several tags have the same type, settings and triggers.",
        "Every matching event is counted more than once, inflating conversions and revenue.", "S", "GTM owner",
        "Preview: exactly one hit per action."),
    "DUPLICATE_GOOGLE_TAG": ("GA4 data quality", "The same Google tag ID is configured by several tags on the same trigger.",
        "Page views and sessions can be double-counted.", "S", "GTM owner", "Network tab: one page_view per page load."),
    "GA4_EVENT_NO_NAME": ("Measurement coverage", "GA4 event tags have an empty event name.", "GA4 drops events without a name.", "S",
        "GTM owner", "DebugView shows the event with its name."),
    "GA4_EVENT_NAME_INVALID": ("Event taxonomy", "Event names break GA4's rules (letters, numbers and underscores only, start with a letter, max 40 characters, no reserved names).",
        "Reserved or over-long names are dropped; names with spaces are inconsistent and break exports and audiences.", "S", "GTM owner + Analytics",
        "DebugView shows the new snake_case name; update reports and key events that use the old name."),
    "GA4_EVENT_NAME_STYLE": ("Event taxonomy", "Event names mix upper and lower case.",
        "GA4 is case-sensitive, so one action splits into several rows.", "S", "Analytics", "Event report shows one row per action."),
    "GA4_TOO_MANY_PARAMS": ("GA4 data quality", "Tags send more than 25 event parameters.", "Parameters beyond the 25th are dropped.", "S",
        "GTM owner", "DebugView shows every parameter you need."),
    "GA4_PARAM_NAME_INVALID": ("GA4 data quality", "Parameter names break GA4's naming rules.",
        "These parameters are dropped and can't be registered as custom dimensions.", "S", "GTM owner", "DebugView shows the renamed parameters."),
    "DYNAMIC_PARAM_NAMES": ("GA4 data quality", "The parameter-name field holds a variable, so every click sends a different, unpredictable parameter key.",
        "The events carry no usable context: you can't report which link or CTA was clicked, and the parameter list grows without limit.", "S",
        "GTM owner", "DebugView: the event shows fixed keys such as link_text and link_url."),
    "DEBUG_MODE_SET": ("GA4 data quality", "debug_mode is set on production tags.",
        "GA4 treats any value as debug traffic: DebugView fills with real users and debug hits may be excluded from standard reports.", "S",
        "GTM owner", "Network tab: production GA4 hits no longer contain _dbg / ep.debug_mode."),
    "ANONYMIZE_IP": ("Tag governance", "GA4 tags still set anonymize_ip.", "GA4 always anonymises IPs; the setting does nothing and signals a copy-paste from UA.",
        "S", "GTM owner", "Setting removed from the tag."),
    "ECOMMERCE_NO_ITEMS": ("Measurement coverage", "Ecommerce events send neither the ecommerce object nor an items parameter.",
        "Ecommerce, item and revenue reports stay empty for these events.", "M", "GTM owner + Web developers",
        "DebugView shows items and value on the event."),
    "LEGACY_UA_TAG": ("Tag governance", "Universal Analytics tags are still active.", "UA no longer processes data; the tags only slow pages down.",
        "S", "GTM owner", "No requests to google-analytics.com/collect (UA) in the Network tab."),
    "LEGACY_GA4_CONFIG": ("Tag governance", "Tags use the old GA4 Configuration tag type.", "No impact today; listed for awareness.", "S",
        "GTM owner", "-"),
    "TAG_NO_TRIGGER": ("Tag governance", "Live tags have no firing trigger and are not used as setup/cleanup tags.",
        "The tag never fires. It may be leftover, or a trigger is missing.", "S", "GTM owner", "Decide: attach a trigger or delete."),
    "GOOGLE_TAG_SETUP_ONLY": ("GA4 data quality", "The Google tag has no firing trigger and only loads as a setup tag of another tag.",
        "GA4 initialises late and only when that other tag fires; early events can be sent without your config settings.", "S", "GTM owner",
        "Preview: the Google tag fires on Initialization - All Pages before any GA4 event."),
    "CUSTOM_HTML_RISK": ("Martech stack", "Custom HTML contains risky code (document.write, eval, plain-http scripts, very large code).",
        "Can slow or break pages and bypasses template permissions.", "M", "Web developers", "Code reviewed; replaced by a gallery template where possible."),
    "CUSTOM_HTML_ALL_PAGES": ("Martech stack", "Custom HTML tags run on every page.", "Extra script on every page load; harder to govern than templates.",
        "M", "GTM owner", "Template used, or trigger narrowed."),
    "PAGE_RELOAD": ("GA4 data quality", "Custom HTML reloads the page.", "Every reload sends another page_view and can restart the session, inflating page views and distorting landing pages.",
        "S", "GTM owner + Web developers", "Accept the cookie banner on a fresh session: exactly one page_view."),
    "SHORT_POLLING": ("Tag governance", "Custom HTML polls with setInterval more often than every 100 ms.", "Wastes CPU on every page, especially on mobile.",
        "S", "Web developers", "Performance profile shows no tight polling loop."),
    "DATALAYER_TYPO": ("Tag governance", "Code pushes to a misspelt data layer (e.g. self.datalayer instead of window.dataLayer).",
        "GTM never sees those events, so triggers listening for them never fire.", "S", "GTM owner", "Preview: the event appears in the event timeline."),
    "COOKIE_WIPE": ("Consent & privacy", "Custom HTML deletes cookies in bulk.",
        "Can remove the consent tool's own state, session cookies and analytics IDs, creating new users and sessions on every page.", "M",
        "GTM owner + Privacy/Legal", "Compare document.cookie before and after page load for each consent state."),
    "CMP_VIA_GTM": ("Consent & privacy", "The consent banner (CMP) script is loaded by GTM.",
        "Race conditions: tags can evaluate before the consent cookie exists on the first page view.", "M", "Web developers + Privacy/Legal",
        "Waterfall: the CMP script is requested before gtm.js."),
    "CONSENT_MODE_MISSING": ("Consent & privacy", "Google Consent Mode v2 defaults/updates are not set by any live tag.",
        "Google Ads and GA4 get no consent signals, so there is no modelling and EEA advertising features can be restricted.", "M",
        "GTM owner + Privacy/Legal", "Tag Assistant 'Consent' tab: defaults before any tag, update after the choice."),
    "CONSENT_NOT_ENFORCED": ("Consent & privacy", "Third-party tags fire with no consent check: no consent settings, no consent condition on the firing trigger and no working exception.",
        "Advertising or tracking scripts can load before or without consent (GDPR/ePrivacy exposure).", "S", "GTM owner + Privacy/Legal",
        "Preview with consent declined: the tag does not fire."),
    "EXCEPTION_EVENT_MISMATCH": ("Consent & privacy", "Exceptions (blocking triggers) only apply when they listen to the same GTM event as the firing trigger. On these tags they never do.",
        "The block you think is there isn't: the tag fires even when the exception's conditions are true.", "S", "GTM owner",
        "Preview with the blocking condition true: the tag shows under 'Tags Not Fired'."),
    "MISSING_CONVERSION_LINKER": ("Measurement coverage", "Google Ads/Floodlight tags run without a Conversion Linker.",
        "Ad click IDs aren't stored first-party, so conversions are under-reported, especially on Safari.", "S", "GTM owner",
        "Cookie _gcl_aw is set after landing with a gclid."),
    "CATCH_ALL_TRIGGER": ("Tag governance", "Custom Event triggers match every dataLayer event (regex .*).",
        "Tags fire on internal GTM events (gtm.js, gtm.dom, gtm.load...), creating unintended hits.", "S", "GTM owner",
        "Preview: tag fires only on the intended events."),
    "REGEX_MATCHES_ALL": ("Tag governance", "Regex conditions contain an empty alternative (a leading/trailing '|' or '||'), which matches everything.",
        "The trigger is much broader than intended and over-counts.", "S", "GTM owner", "Preview: non-target interactions don't fire the tag."),
    "CLICK_VARS_ON_PAGE_LOAD": ("Tag governance", "Page-load triggers (Page View, DOM Ready, Window Loaded) test click or form variables, which are empty at page load.",
        "The condition is never true (or always true when negated), so the trigger doesn't do what its name says.", "S", "GTM owner",
        "Preview on page load: the condition evaluates as intended."),
    "DUPLICATE_TRIGGER": ("Tag governance", "Several triggers have identical type and conditions.", "Changes to one copy don't apply to the other.",
        "S", "GTM owner", "-"),
    "HARDCODED_MEASUREMENT_ID": ("Tag governance", "A measurement ID is typed into several tags instead of a variable.",
        "Switching or adding a property means editing every tag.", "S", "GTM owner", "Tags reference one Constant/Lookup variable."),
    "MIXED_MEASUREMENT_ROUTING": ("GA4 data quality", "Most GA4 tags route to properties through a variable, but these tags hard-code a measurement ID.",
        "Their events go to one property regardless of market/site, so other properties miss them.", "S", "GTM owner",
        "Preview on each market: the hit's tid matches that market's property."),
    "LOOKUP_NO_DEFAULT": ("GA4 data quality", "Lookup/RegEx tables have no default value.",
        "Any unmapped input (new locale, staging host) returns undefined; if it feeds a measurement ID, data is lost.", "S", "GTM owner",
        "Preview on an unmapped page: the variable returns the default."),
    "PAUSED_TAG": ("Tag governance", "Tags are paused.", "Clutter; a paused tag can be re-enabled by mistake. They remain in version history if deleted.",
        "S", "GTM owner", "0 paused tags."),
    "TRIGGERS_ONLY_PAUSED": ("Tag governance", "Triggers are used only by paused tags.", "Dead configuration that makes the container harder to review.",
        "S", "GTM owner", "Deleted together with the paused tags."),
    "UNUSED_TRIGGER": ("Tag governance", "Triggers aren't used by any tag.", "Clutter; makes the container harder to review.", "S", "GTM owner", "0 unused triggers."),
    "UNUSED_VARIABLE": ("Tag governance", "Variables aren't referenced anywhere.", "Clutter; makes the container harder to review.", "S", "GTM owner", "0 unused variables."),
    "NAMING_CONVENTION": ("Tag governance", "Tag names don't follow a consistent convention.", "Inconsistent names make the container slow to audit and easy to break.",
        "M", "GTM owner", "Names follow 'Platform - Type - Detail'."),
    "NO_FOLDERS": ("Tag governance", "Most items aren't in folders.", "Large containers without folders are hard to navigate.", "S", "GTM owner", "-"),
    "NO_CONVERSION_EVENT": ("Measurement coverage", "No live GA4 tag sends a conversion-type event (generate_lead, sign_up, purchase, form submit...).",
        "GA4 can't report the outcomes the site exists for; ad platforms optimise for clicks instead of results.", "M",
        "Web developers + GTM owner", "Submit a test form/order: exactly one conversion event in DebugView."),
}

SEVERITY_LABEL = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low", "info": "Info"}


def meta(rule: str) -> dict:
    area, what, impact, effort, owner, validate = RULE_META.get(rule, ("Tag governance", "", "", "M", "GTM owner", ""))
    return {"area": area, "what": what, "impact": impact, "effort": effort, "owner": owner, "validate": validate}


def priority(severity: str, effort: str) -> str:
    if severity in ("critical", "high"):
        return "P1 - Quick win" if effort == "S" else "P1 - Structural"
    return "P2" if severity == "medium" else "P3"


def phase(severity: str, effort: str) -> str:
    if severity == "critical" or (severity == "high" and effort == "S"):
        return "1 - Now (0-2 weeks)"
    if severity in ("high", "medium"):
        return "2 - Next (within 60 days)"
    return "3 - Later (within 90 days)"


def health(counts: dict) -> str:
    if counts.get("critical"):
        return "Red"
    if counts.get("high"):
        return "Amber"
    if counts.get("medium"):
        return "Watch"
    return "Green"


LEGEND = [
    ("Scope", "Static review of the live (published) container version read through the GTM API. No GTM Preview, network or GA4 data is used; "
              "items marked [verify] need confirmation in Preview."),
    ("Severity: Critical", "Data is wrong, there is legal exposure, or conversions are lost."),
    ("Severity: High", "Material data-quality or compliance weakness; fix within 30 days."),
    ("Severity: Medium", "Reduces reliability, maintainability or value; fix in the roadmap."),
    ("Severity: Low", "Hygiene and best practice."),
    ("Effort", "S = GTM-only change under a day; M = GTM plus developer or legal input; L = multi-team project."),
    ("Priority", "Critical/High + S = P1 Quick win; Critical/High + M/L = P1 Structural; Medium = P2; Low = P3."),
    ("Phase", "Critical, or High + S = Now (0-2 weeks); other High and Medium = Next (within 60 days); Low = Later (within 90 days)."),
    ("Health", "Red = any Critical; Amber = any High; Watch = Medium only; Green = none of these."),
    ("Expert review", "Optional AI review (Claude) that adds context-specific findings, priorities and an event model. Marked 'Expert review'; verify before acting."),
]
