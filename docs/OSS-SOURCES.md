# Источники рабочего dashboard

- [Infinity JQ backend](https://grafana.com/docs/plugins/yesoreyeram-infinity-datasource/latest/query/) — REST queries, numeric/text tables, server-side alerting.
- [Grafana v2 schema](https://grafana.com/docs/grafana/latest/observability-as-code/schema-v2/) — RowsLayout, QueryGroup and AnnotationQuery.
- [TeamCity Server Health](https://www.jetbrains.com/help/teamcity/rest/healthitem.html) — global health warnings.
- [Jenkins Remote API](https://www.jenkins.io/doc/book/using/remote-access-api/) — job/folder/computer/queue resources.
- [Octopus tasks](https://octopus.com/docs/api/tasks) — environment and from/toCompletedDate filters.
- [Octopus deployments](https://octopus.com/docs/api/deployments) — TaskId, ProjectId, ReleaseId and DeployedBy linkage.
- [Kube-state-metrics pods](https://github.com/kubernetes/kube-state-metrics/blob/main/docs/metrics/workload/pod-metrics.md) — pod conditions, restarts, requests/limits.

Текущий dashboard сохраняет endpoint queries из переданного экспорта. Prometheus Plugin, native /app/metrics и blackbox не требуются. Собственный сборщик метрик не добавлен; Python scripts только собирают JSON и alert examples.
