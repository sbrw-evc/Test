# Выбор источников без собственного exporter

Проверено 06 октября 2026 по документации и исходному коду. Используем upstream компоненты без изменений; в репозитории только конфигурация, offline generator и тесты query expressions.

| Решение | Выбор | Подтверждённые данные и ограничения |
|---|---|---|
| TeamCity `/app/metrics` | Основной native источник | Connected+authorized agents, running/queued builds, сервер/JVM/HTTP. `max` по HA nodes, не сумма. Набор сверить с Diagnostics Metrics своей версии |
| [jenkinsci/prometheus-plugin](https://github.com/jenkinsci/prometheus-plugin) | Основной Jenkins источник, Apache-2.0 | Last ordinal, duration ms, current duration ms, node online, executors per label, queue per label, build counters. Default prefix `default_jenkins`, job attribute `jenkins_job` надо согласовать |
| [grafana/grafana-infinity-datasource](https://github.com/grafana/grafana-infinity-datasource) | Основной REST источник, Apache-2.0 | TeamCity per-build данные + Octopus task data напрямую из API. `parser: backend` — JSONata, поддерживает серверный alerting. Frontend/UQL для alert rules не использовать |
| [prometheus/blackbox_exporter](https://github.com/prometheus/blackbox_exporter) | HTTP availability/latency, Apache-2.0 | probe_success и probe_duration_seconds; UP scrape exporter не равен успешному HTTP probe |
| [octopusden/octopus-teamcity-prometheus-exporter](https://github.com/octopusden/octopus-teamcity-prometheus-exporter) | Готовая OSS альтернатива TC, не устанавливаем в основной схеме | README описывает last status по templates; текущий код также содержит last SUCCESS duration, known-answer probe counter и расширенные метрики. Gauges остаются прежними при ошибке API; статус и duration могут обновляться разными циклами. Для достоверных свежих per-build запросов выбран Infinity |
| [Guidewire/teamcity_exporter](https://github.com/Guidewire/teamcity_exporter) | Не выбран | Экспортирует build statistics; старый build workflow с Go1.8, не универсальный health exporter. Требует отдельной оценки совместимости/поддержки |
| [OctopusDeploy/OctopusGrafanaDataSource](https://github.com/OctopusDeploy/OctopusGrafanaDataSource) | Историческая альтернатива | Community dashboard13413 использует XML-feed datasource. Для серверных REST alerts выбран поддерживаемый Infinity |

По рассмотренным источникам не подтверждён готовый Octopus Deploy Prometheus exporter, покрывающий все необходимые KPI. Это не утверждение, что таких проектов вообще нет. Предложенное REST/Infinity решение соответствует исходному варианту задачи и не требует писать exporter. `octopus-agile-exporter` относится к Octopus Energy, не Deploy.

## Что взято из аналогичных dashboards

- [TeamCity11669](https://grafana.com/grafana/dashboards/11669-teamcity/) и [JetBrains health example](https://blog.jetbrains.com/teamcity/2022/06/monitoring-teamcity-server-health/): health/load, queues, agents, HA aggregation.
- [Jenkins9964](https://grafana.com/grafana/dashboards/9964-jenkins-performance-and-health-overview/): nodes, executors, queue, job result и duration.
- [Octopus11720](https://grafana.com/grafana/dashboards/11720-octopus-deploy/): deployments/tasks и project scope. Этот dashboard требует SQL Server access; такой доступ в нашей схеме не нужен.
- [Octopus13413](https://grafana.com/grafana/dashboards/13413-octopus-deployments-overview/): project/environment view.

Комплект содержит новый dashboard JSON для Native+REST, а не непроверенный импорт старого SQL/Graph/Singlestat dashboard.

## Первичные контракты

- [TeamCity native metrics и readiness](https://www.jetbrains.com/help/teamcity/teamcity-monitoring-and-diagnostics.html)
- [TeamCity metrics at JetBrains](https://blog.jetbrains.com/teamcity/2022/06/monitoring-teamcity-server-health/)
- [Jenkins metrics](https://github.com/jenkinsci/prometheus-plugin/blob/master/docs/metrics/index.md)
- [Jenkins ExecutorCollector label model](https://github.com/jenkinsci/prometheus-plugin/blob/master/src/main/java/org/jenkinsci/plugins/prometheus/ExecutorCollector.java)
- [Infinity JSONata/backend](https://grafana.com/docs/plugins/yesoreyeram-infinity-datasource/latest/query/backend/)
- [Infinity provisioning](https://grafana.com/docs/plugins/yesoreyeram-infinity-datasource/latest/configure/)
- [Octopus tasks filters and TotalResults](https://octopus.com/docs/api/tasks)
- [Grafana table alert requirements](https://grafana.com/docs/grafana/latest/alerting/fundamentals/alert-rules/queries-conditions/)
