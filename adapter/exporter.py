"""Read-only snapshot exporter. No event counters or inferred worker capacity."""
import base64
import json
import logging
import os
import re
import ssl
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler

import yaml
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily

LOG = logging.getLogger("cicd")
TERMINAL = {"SUCCESS": 0, "Success": 0, "UNSTABLE": 1, "FAILURE": 2,
            "Failed": 2, "ABORTED": 3, "Canceled": 3, "TimedOut": 4,
            "NOT_BUILT": 5, "UNKNOWN": 5}


def stamp(value):
    if not value:
        raise ValueError("Required timestamp missing")
    if re.match(r"^\d{8}T\d{6}[+-]\d{4}$", value):
        return datetime.strptime(value, "%Y%m%dT%H%M%S%z").timestamp()
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must have timezone")
    return result.timestamp()


class NoRedirect(HTTPRedirectHandler):
    # Never forward API credentials to a redirected host or a login page.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, config):
        self.config = config
        self.reached = False
        self.base = config["url"].rstrip("/") + "/"
        parsed = urlsplit(self.base)
        if parsed.scheme not in ("https", "http") or parsed.username or parsed.password:
            raise ValueError("Use HTTP(S) URL without credentials")
        self.headers = {"Accept": "application/json"}
        token = os.environ[config["token_env"]]
        if config["component"] == "octopus":
            self.headers["X-Octopus-ApiKey"] = token
        elif config["component"] == "jenkins":
            user = os.environ[config["user_env"]]
            self.headers["Authorization"] = "Basic " + base64.b64encode(
                (user + ":" + token).encode()).decode()
        else:
            self.headers["Authorization"] = "Bearer " + token
        context = ssl.create_default_context(cafile=config.get("ca_file"))
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=context))

    def get(self, path, params=None):
        # Absolute paths returned by TeamCity pagination must stay on this origin.
        url = urljoin(self.base, path)
        if urlsplit(url).netloc != urlsplit(self.base).netloc or urlsplit(url).scheme != urlsplit(self.base).scheme:
            raise ValueError("Cross-origin pagination rejected")
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        request = Request(url, headers=self.headers, method="GET")
        with self.opener.open(request, timeout=self.config.get("request_timeout", 10)) as response:
            self.reached = True
            return json.load(response)

    def tc_pages(self, path, key, params):
        result = []
        for _ in range(self.config.get("max_pages", 50)):
            page = self.get(path, params)
            result.extend(page[key])
            path, params = page.get("nextHref"), None
            if not path:
                return result
        raise ValueError("TeamCity pagination limit exceeded")

    def oct_pages(self, path, params):
        result = []
        for i in range(self.config.get("max_pages", 50)):
            page = self.get(path, dict(params, skip=i * 100, take=100))
            items = page["Items"]
            result.extend(items)
            if len(result) >= page["TotalResults"]:
                return result
            if not items:
                raise ValueError("Octopus incomplete pagination")
        raise ValueError("Octopus pagination limit exceeded")


def metric(rows, name, value, **labels):
    rows.append((name, labels, float(value)))


def last(rows, pipeline, operation, status, started, completed):
    labels = dict(project=pipeline["project"], pipeline=pipeline["pipeline"], operation=operation)
    metric(rows, "ci_last_operation_status", status, **labels)
    metric(rows, "ci_last_operation_completed_timestamp_seconds", completed, **labels)
    if started is not None:
        metric(rows, "ci_last_operation_duration_seconds", max(0, completed-started), **labels)


def running(rows, pipeline, operation, starts, now):
    labels = dict(project=pipeline["project"], pipeline=pipeline["pipeline"], operation=operation)
    metric(rows, "ci_running_elapsed_seconds", max([max(0, now-s) for s in starts] or [0]), **labels)
    metric(rows, "ci_operation_timeout_seconds", pipeline.get("timeout_seconds", 3600), **labels)


def resources(rows, source, online_ids):
    # Only stable explicitly expected agents. Missing expected ID means offline.
    for identity in source.get("expected_agents", []):
        metric(rows, "ci_resource_online", int(str(identity) in online_ids), resource_id=str(identity))


def collect_teamcity(client, now):
    rows, source = [], client.config
    agents = client.tc_pages("app/rest/agents", "agent", {
        "locator": "defaultFilter:false,count:100",
        "fields": "nextHref,agent(id,connected,authorized,enabled)"})
    online = {str(a["id"]) for a in agents if a.get("connected") is True
              and a.get("authorized") is True and a.get("enabled") is True}
    metric(rows, "ci_agents_online", len(online))
    resources(rows, source, online)
    queue = []
    for p in source["pipelines"]:
        identifier = p["build_type"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise ValueError("Invalid TeamCity build type ID")
        common = "buildType:(id:" + identifier + "),defaultFilter:false"
        done = client.get("app/rest/builds", {
            "locator": common + ",state:finished,count:1",
            "fields": "build(id,status,canceledInfo,startDate,finishDate)"})["build"]
        if done:
            b = done[0]
            status = 3 if "canceledInfo" in b else TERMINAL.get(b["status"], 5)
            last(rows, p, "build", status, stamp(b["startDate"]) if b.get("startDate") else None,
                 stamp(b["finishDate"]))
        active = client.tc_pages("app/rest/builds", "build", {
            "locator": common + ",state:running,count:100", "fields": "nextHref,build(startDate)"})
        running(rows, p, "build", [stamp(b["startDate"]) for b in active], now)
        queue.extend(client.tc_pages("app/rest/buildQueue", "build", {
            "locator": "buildType:(id:" + identifier + "),count:100",
            "fields": "nextHref,build(queuedDate)"}))
    metric(rows, "ci_queue_length", len(queue))
    metric(rows, "ci_queue_oldest_age_seconds", max([max(0, now-stamp(b["queuedDate"])) for b in queue] or [0]))
    return rows


def collect_jenkins(client, now):
    rows, source = [], client.config
    computers = client.get("computer/api/json", {"tree": "computer[displayName,offline,numExecutors]"})["computer"]
    # Controller excluded unless explicitly configured by its API displayName.
    configured = source.get("agent_names")
    computers = [a for a in computers if a["displayName"] in configured] if configured is not None else [
        a for a in computers if a["displayName"] not in ("Built-In Node", "master")]
    online = {a["displayName"] for a in computers if not a["offline"] and a["numExecutors"] > 0}
    metric(rows, "ci_agents_online", len(online))
    resources(rows, source, online)
    paths = set()
    for p in source["pipelines"]:
        path = p["job_path"].strip("/") + "/"
        if not path.startswith("job/") or ".." in path or "?" in path:
            raise ValueError("Use encoded Jenkins job path")
        paths.add(urlsplit(urljoin(client.base, path)).path.rstrip("/"))
        info = client.get(path + "api/json", {"tree":
            "lastCompletedBuild[number,result,timestamp,duration]"})
        done = info.get("lastCompletedBuild")
        if done:
            started = done["timestamp"] / 1000
            last(rows, p, "build", TERMINAL.get(done["result"], 5), started, started + done["duration"] / 1000)
        builds = []
        for page in range(source.get("max_pages", 50)):
            batch = client.get(path + "api/json", {"tree":
                "builds[number,building,timestamp]{%d,%d}" % (page*100, (page+1)*100)})["builds"]
            builds.extend(batch)
            if len(batch) < 100:
                break
        else:
            raise ValueError("Jenkins active-build scan limit exceeded")
        running(rows, p, "build", [b["timestamp"] / 1000 for b in builds if b["building"]], now)
    queued = client.get("queue/api/json", {"tree": "items[inQueueSince,task[url]]"})["items"]
    queue = [b for b in queued if urlsplit(b.get("task", {}).get("url", "")).path.rstrip("/") in paths]
    metric(rows, "ci_queue_length", len(queue))
    metric(rows, "ci_queue_oldest_age_seconds", max([max(0, now-b["inQueueSince"]/1000) for b in queue] or [0]))
    return rows


def collect_octopus(client, now):
    rows, source, queue = [], client.config, []
    path = "api/" + source["space"] + "/tasks"
    for p in source["pipelines"]:
        params = {"name": "Deploy", "project": p["project_id"], "environment": p["environment_id"]}
        # API returns newest first. One terminal result, all active Deploy tasks.
        done = client.get(path, dict(params, active="false", take=1))["Items"]
        if done:
            b = done[0]
            last(rows, p, "deploy", TERMINAL.get(b["State"], 5),
                 stamp(b["StartTime"]) if b.get("StartTime") else None, stamp(b["CompletedTime"]))
        active = client.oct_pages(path, dict(params, active="true"))
        queue.extend(b for b in active if b["State"] in ("New", "Queued"))
        running(rows, p, "deploy", [stamp(b["StartTime"]) for b in active
                                    if b["State"] in ("Executing", "Cancelling")], now)
    metric(rows, "ci_queue_length", len(queue))
    metric(rows, "ci_queue_oldest_age_seconds", max([max(0, now-stamp(b["QueueTime"])) for b in queue] or [0]))
    return rows


COLLECTORS = {"teamcity": collect_teamcity, "jenkins": collect_jenkins, "octopus": collect_octopus}


class Exporter:
    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.states = {}
        components = [s["component"] for s in config["sources"]]
        if sorted(components) != sorted(COLLECTORS):
            raise ValueError("Configure exactly one source per component")
        for source in config["sources"]:
            if not source.get("pipelines"):
                raise ValueError("Explicit nonempty pipelines required")
            ids = [(p["project"], p["pipeline"]) for p in source["pipelines"]]
            if len(set(ids)) != len(ids):
                raise ValueError("Duplicate pipeline labels")
            scopes = [p["build_type"] if source["component"] == "teamcity" else p["job_path"].strip("/")
                      if source["component"] == "jenkins" else (p["project_id"], p["environment_id"])
                      for p in source["pipelines"]]
            if len(set(scopes)) != len(scopes):
                raise ValueError("Duplicate source scopes would double-count queues")
            self.states[source["component"]] = dict(rows=[], api=None, complete=0, success=0)

    def poll_source(self, source):
        component = source["component"]
        client = None
        try:
            client = Client(source)
            rows = COLLECTORS[component](client, time.time())
            metric(rows, "ci_queue_limit", source.get("queue_limit", 10))
            metric(rows, "ci_agents_minimum", source.get("agents_minimum", 1)) if component != "octopus" else None
            with self.lock:
                self.states[component] = dict(rows=rows, api=1, complete=1, success=time.time())
        except Exception as exc:
            # Error text may include URLs or credentials; only log type and HTTP code.
            LOG.warning("collection failed component=%s error=%s code=%s", component,
                        type(exc).__name__, getattr(exc, "code", "-"))
            with self.lock:
                self.states[component]["api"] = int(isinstance(exc, (ValueError, KeyError, TypeError))
                                                     and client is not None and client.reached)
                self.states[component]["complete"] = 0

    def worker(self, source):
        while True:
            self.poll_source(source)
            time.sleep(self.config.get("poll_seconds", 30))

    def collect(self):
        with self.lock:
            states = {c: dict(s, rows=list(s["rows"])) for c, s in self.states.items()}
        families = {}
        for component, state in states.items():
            rows = list(state["rows"])
            for name, key in [("ci_api_up", "api"), ("ci_collection_complete", "complete"),
                              ("ci_collection_last_success_timestamp_seconds", "success")]:
                if state[key] is not None:
                    metric(rows, name, state[key])
            for name, labels, value in rows:
                labels = dict(env=self.config.get("env", "prod"), component=component, **labels)
                keys = tuple(sorted(labels))
                if name not in families:
                    families[name] = GaugeMetricFamily(name, name.replace("_", " "), labels=keys)
                families[name].add_metric([labels[k] for k in keys], value)
        yield from families.values()


def serve(config):
    exporter = Exporter(config)
    registry = CollectorRegistry()
    registry.register(exporter)
    for source in config["sources"]:
        threading.Thread(target=exporter.worker, args=(source,), daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/metrics":
                body, status, content_type = generate_latest(registry), 200, "text/plain; version=0.0.4; charset=utf-8"
            elif self.path in ("/healthz", "/readyz"):
                # Liveness checks process only; upstream outages must stay observable.
                body, status, content_type = b"ok\n", 200, "text/plain"
            else:
                body, status, content_type = b"not found\n", 404, "text/plain"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    ThreadingHTTPServer(("0.0.0.0", config.get("port", 8000)), Handler).serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    with open(os.environ.get("CONFIG_FILE", "/config/config.yml"), encoding="utf-8") as stream:
        serve(yaml.safe_load(stream))
