# CI/CD snapshot exporter

Read-only REST adapter for TeamCity, Jenkins and Octopus Deploy. Full setup: [Russian steps](../docs/SETUP-RU.md); metric semantics: [contract](../cicd-health/METRICS-CONTRACT.md).

## Run and test locally

Requires Python 3.12. Secrets must be supplied through the environment (same keys as Kubernetes Secret). Copy config.example.yml to config.yml and replace URLs/IDs.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
CONFIG_FILE=config.yml python exporter.py
curl -fsS http://localhost:8000/metrics
```

Only /metrics, /healthz, /readyz are exposed, on port8000. /healthz and /readyz check HTTP process, not upstream availability. Kubernetes templates: ../kubernetes/adapter.yml and optional CA patch. Container runs nonroot with read-only filesystem; no persistent state required.

Implemented snapshots: last terminal status/duration, max active elapsed, selected-scope queue, TC/Jenkins online agents, configured stable-agent presence and freshness. No replay/durable event counting or historical histogram. Each component polls independently and commits its gauges only after a complete collection. API failures remain visible through gauges; previous data is retained and suppressed by dashboard freshness conditions.

First install on a small scope and run smoke checks against your versions. Tests use local mock HTTP APIs; they do not certify access to the real cluster or a production deployment. The image has not been built in this workspace (Docker unavailable). Build in your CI, scan dependencies/image according to your normal process, and publish an immutable digest.
