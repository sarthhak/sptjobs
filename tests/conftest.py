import copy
import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ALL_PAGES = "2147479553"


def p(key, value, ptype="template"):
    return {"type": ptype, "key": key, "value": value}


def ce_filter(event, match="equals"):
    return [{"type": match, "parameter": [p("arg0", "{{_event}}"), p("arg1", event)]}]


def messy_version():
    """A container with one or more examples of most problems the audit looks for."""
    return {
        "containerVersionId": "42",
        "path": "accounts/1/containers/2/versions/42",
        "container": {"name": "Example site", "publicId": "GTM-TEST1"},
        "builtInVariable": [{"name": "Page URL", "type": "pageUrl"}, {"name": "Page Path", "type": "pagePath"}, {"name": "Event", "type": "event"}],
        "variable": [
            {"variableId": "1", "name": "DLV - transaction_id", "type": "v", "parameter": [p("name", "ecommerce.transaction_id")]},
            {"variableId": "2", "name": "JS - unused", "type": "jsm", "parameter": [p("javascript", "function(){return 1}")]},
            {"variableId": "3", "name": "Lookup - Env", "type": "smm", "parameter": [p("input", "{{Page Hostname}}")]},
        ],
        "trigger": [
            {"triggerId": "10", "name": "CE - purchase", "type": "customEvent", "customEventFilter": ce_filter("purchase")},
            {"triggerId": "11", "name": "CE - purchase copy", "type": "customEvent", "customEventFilter": ce_filter("purchase")},
            {"triggerId": "12", "name": "Click - unused", "type": "click"},
            {"triggerId": "13", "name": "CE - all events", "type": "customEvent", "customEventFilter": ce_filter(".*", "matchRegex")},
        ],
        "tag": [
            {"tagId": "1", "name": "GA4 - Google Tag - All Pages", "type": "googtag", "parameter": [p("tagId", "G-ABC123")], "firingTriggerId": [ALL_PAGES]},
            {"tagId": "2", "name": "GA4 - Event - purchase", "type": "gaawe", "firingTriggerId": ["10"],
             "parameter": [p("eventName", "purchase"), p("measurementIdOverride", "G-ABC123"),
                           {"type": "list", "key": "eventSettingsTable", "list": [{"type": "map", "map": [p("parameter", "transaction_id"), p("parameterValue", "{{DLV - transaction_id}}")]}]}]},
            {"tagId": "3", "name": "GA4 - Event - purchase (copy)", "type": "gaawe", "firingTriggerId": ["10"],
             "parameter": [p("eventName", "purchase"), p("measurementIdOverride", "G-ABC123"),
                           {"type": "list", "key": "eventSettingsTable", "list": [{"type": "map", "map": [p("parameter", "transaction_id"), p("parameterValue", "{{DLV - transaction_id}}")]}]}]},
            {"tagId": "4", "name": "GA4 Event Sign Up", "type": "gaawe", "firingTriggerId": ["11"],
             "parameter": [p("eventName", "Sign Up"), p("measurementIdOverride", "G-ABC123")]},
            {"tagId": "5", "name": "GA4 - Event - lead", "type": "gaawe", "firingTriggerId": ["10"], "parameter": [p("eventName", "generate_lead")]},
            {"tagId": "6", "name": "UA - Pageview - All Pages", "type": "ua", "firingTriggerId": [ALL_PAGES], "parameter": [p("trackingId", "UA-1-1")]},
            {"tagId": "7", "name": "HTML - Old - test", "type": "html", "paused": True, "parameter": [p("html", "<script>1</script>")]},
            {"tagId": "8", "name": "HTML - Pixel - all events", "type": "html", "firingTriggerId": ["13"],
             "parameter": [p("html", "<script>document.write('<img src=x>')</script>")]},
            {"tagId": "9", "name": "HTML - Env - All Pages", "type": "html", "firingTriggerId": [ALL_PAGES],
             "consentSettings": {"consentStatus": "needed", "consentType": {"type": "list", "list": [p("", "analytics_storage")]}},
             "parameter": [p("html", "<script>var env='{{Lookup - Env}}'</script>")]},
            {"tagId": "10", "name": "Ads - Conversion - purchase", "type": "awct", "firingTriggerId": ["10"], "parameter": [p("conversionId", "123")]},
            {"tagId": "11", "name": "GA4 - Event - orphan", "type": "gaawe", "parameter": [p("eventName", "orphan_event"), p("measurementIdOverride", "G-ABC123")]},
            {"tagId": "12", "name": "GA4 - Event - broken", "type": "gaawe", "firingTriggerId": ["999"],
             "parameter": [p("eventName", "broken_event"), p("measurementIdOverride", "G-ABC123")]},
        ],
    }


@pytest.fixture
def version():
    return messy_version()


class FakeGTM:
    """In-memory stand-in for GTMClient with the same method names."""

    KEYS = {"tag": ("tag", "tagId"), "trigger": ("trigger", "triggerId"), "variable": ("variable", "variableId")}

    def __init__(self, version, container_path="accounts/1/containers/2"):
        self.container_path = container_path
        self.versions = {version["containerVersionId"]: copy.deepcopy(version)}
        self.live_id = version["containerVersionId"]
        self.latest_id = version["containerVersionId"]
        self.workspaces = {}
        self._ids = itertools.count(1000)
        self.calls = []
        self.fail_on = None  # e.g. ("update", "tag")

    # read
    def list_containers(self):
        return [{"account_name": "Acct", "container_name": "Example site", "public_id": "GTM-TEST1", "container_path": self.container_path}]

    def get_live_version(self, path):
        return copy.deepcopy(self.versions[self.live_id])

    def get_latest_version_header(self, path):
        return {"containerVersionId": self.latest_id}

    def get_workspace_status(self, ws):
        return {}

    def _ws_items(self, ws, kind):
        return self.workspaces[ws][self.KEYS[kind][0]]

    def get_entity(self, ws, kind, eid):
        idkey = self.KEYS[kind][1]
        for e in self._ws_items(ws, kind):
            if e[idkey] == eid:
                return {**copy.deepcopy(e), "fingerprint": e.get("fingerprint", "fp0"), "workspaceId": ws.rsplit("/", 1)[1]}
        raise KeyError(f"{kind} {eid} not found")

    # write
    def create_workspace(self, container_path, name, description=""):
        wid = str(next(self._ids))
        path = f"{container_path}/workspaces/{wid}"
        base = copy.deepcopy(self.versions[self.latest_id])
        self.workspaces[path] = {"tag": base.get("tag", []), "trigger": base.get("trigger", []), "variable": base.get("variable", []),
                                 "builtInVariable": base.get("builtInVariable", []), "container": base.get("container")}
        self.calls.append(("create_workspace", path))
        return {"path": path, "name": name, "tagManagerUrl": f"https://tagmanager.google.com/#/{path}"}

    def delete_workspace(self, ws):
        self.calls.append(("delete_workspace", ws))
        self.workspaces.pop(ws, None)

    def create_entity(self, ws, kind, body):
        if self.fail_on == ("create", kind):
            raise RuntimeError("boom")
        idkey = self.KEYS[kind][1]
        ent = {**copy.deepcopy(body), idkey: str(next(self._ids))}
        self._ws_items(ws, kind).append(ent)
        self.calls.append(("create", kind, ent[idkey]))
        return ent

    def update_entity(self, ws, kind, eid, body, fingerprint):
        if self.fail_on == ("update", kind):
            raise RuntimeError("boom")
        idkey = self.KEYS[kind][1]
        items = self._ws_items(ws, kind)
        for i, e in enumerate(items):
            if e[idkey] == eid:
                clean = {k: v for k, v in body.items() if k not in ("fingerprint", "workspaceId")}
                items[i] = {**clean, "fingerprint": "fp1"}
                self.calls.append(("update", kind, eid))
                return items[i]
        raise KeyError(eid)

    def delete_entity(self, ws, kind, eid):
        idkey = self.KEYS[kind][1]
        items = self._ws_items(ws, kind)
        items[:] = [e for e in items if e[idkey] != eid]
        self.calls.append(("delete", kind, eid))

    def create_version(self, ws, name, notes):
        vid = str(int(self.latest_id) + 1)
        data = self.workspaces.pop(ws)
        version = {"containerVersionId": vid, "path": f"{self.container_path}/versions/{vid}", "name": name, "notes": notes,
                   "tag": [{k: v for k, v in e.items() if k != "fingerprint"} for e in data["tag"]],
                   "trigger": data["trigger"], "variable": data["variable"], "builtInVariable": data["builtInVariable"],
                   "container": data["container"]}
        self.versions[vid] = version
        self.latest_id = vid
        self.calls.append(("create_version", vid))
        return {"containerVersion": version}

    def publish_version(self, version_path):
        vid = version_path.rsplit("/", 1)[1]
        self.live_id = vid
        self.calls.append(("publish", vid))
        return {"containerVersion": self.versions[vid]}


@pytest.fixture
def fake_gtm(version):
    return FakeGTM(version)


class FakeGA4:
    def list_properties(self):
        return [{"account": "Acct", "property": "properties/123", "property_name": "Site"}]

    def metadata(self, prop):
        return {"dimensions": [{"api_name": "date"}], "metrics": [{"api_name": "sessions"}]}

    def run_report(self, prop, spec):
        rows = [{"date": "20261102", "sessionDefaultChannelGroup": "Organic Search", "sessions": 120},
                {"date": "20261101", "sessionDefaultChannelGroup": "Direct", "sessions": 80}]
        return {"columns": ["date", "sessionDefaultChannelGroup", "sessions"], "dimensions": ["date", "sessionDefaultChannelGroup"],
                "metrics": ["sessions"], "rows": rows, "row_count": 2, "warnings": []}

    def run_realtime(self, prop, dims, mets, limit=50):
        return {"columns": ["activeUsers"], "rows": [{"activeUsers": 3}], "row_count": 1, "warnings": []}

    def property_config(self, prop):
        return {"checks": []}
