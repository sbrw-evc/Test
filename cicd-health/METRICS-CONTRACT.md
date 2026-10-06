# Реальные источники KPI

Собственного exporter нет. `ci:*` — только Prometheus recording rules над native metrics и blackbox; `ci_*` gauges прежнего адаптера больше не используются. REST запросы выполняются непосредственно Grafana Infinity на сервере, не в браузере. Источники сравниваются в docs/OSS-SOURCES.md.

| KPI | Источник | Реальный смысл |
|---|---|---|
| TC агенты | agents_connected_authorized_number | Connected+authorized; не enabled/free/compatible |
| TC очередь | builds_queued_number | Вся доступная серверная очередь, `max` по HA nodes |
| TC последний result/duration | TeamCity REST builds, Infinity | Один настроенный buildType, latest terminal run; duration finish-start |
| TC текущая duration | TeamCity REST running builds | Максимум age среди первых100; nextHref => query error, не частичный success |
| TC постоянный агент | TeamCity REST agents locator ID | Connected+authorized+enabled; отсутствие заданного ID =0 |
| Jenkins последний result | default_jenkins_builds_last_build_result_ordinal | 0 SUCCESS,1 UNSTABLE,2 FAILURE,3 NOT_BUILT,4 ABORTED. Boolean last_build_result считает UNSTABLE успешным, поэтому не используем его |
| Jenkins duration | default_jenkins_builds_last_build_duration_milliseconds | Последняя build duration ms/1000 |
| Jenkins current duration | default_jenkins_builds_running_build_duration_milliseconds | Current run age ms/1000; проверить наличие в установленном plugin |
| Jenkins agents | default_jenkins_nodes_online{node=...} | Online node gauge; sum только по выбранному node regex, controller исключён |
| Jenkins executors/queue | default_jenkins_executors_idle / queue_length{label=...} | ОДИН label pool; не суммировать пересекающиеся labels |
| Octopus последний deploy | `/api/{space}/tasks?name=Deploy&active=false&project=...&environment=...&take=1` | Latest-created terminal Deploy task, не max CompletedTime и не RunbookRun |
| Octopus очередь | tasks с states=Queued, TotalResults | Число queued deployments выбранного project/environment; не количество Items |
| Octopus текущая duration | tasks running=true, take100 | Максимум StartTime age; TotalResults>count(Items) => query error |
| Octopus duration | CompletedTime-StartTime | Последний terminal deploy; -1 если нет run/start |
| Availability / latency | probe_success, probe_duration_seconds | HTTP reachability/readiness и полная DNS/TCP/TLS/HTTP duration |

## Статусы

0 DOWN — наблюдаемая HTTP/native scrape ошибка. 1 UNKNOWN — отсутствующее обязательное наблюдение или нет последнего запуска. 2 DEGRADED — очередь/число агентов за порогом либо неуспешный последний результат. 3 HEALTHY — выбранные проверки в норме.

Infrastructure recordings знают HTTP и native TC/Jenkins данные. Upper health cards объединяют их с REST result в Grafana server-side Math. Общий health относится к **фиксированным scopes из monitoring-config.yml**, не ко всем незаданным проектам. Infinity query error остаётся error/UNKNOWN; не подставляется здоровое состояние. Scalar queries без labels делают Math joins однозначными. Пустые REST истории дают NO RUNS/UNKNOWN. Ошибки схемы и обрезанная running-page дают query error.

Native availability фильтрует старые scrape samples >90s. Это не доказательство свежести внутреннего async Jenkins collector; scrape UP может отдавать его прежний snapshot. Проверить internal plugin interval и версию. Для защиты именно от остановки internal collection нужен поддерживаемый данной версией сигнал; не вводить выдуманный freshness metric.

## Алерты и scope

Grafana Alerting выполняет fixed URL/backend JSONata запросы даже при закрытом dashboard. Alert rules не используют dashboard variables. Табличный Infinity result содержит только один numeric field. execErrState=Alerting; noDataState=Alerting для обязательных KPI. У absent(...) правил noDataState=OK, чтобы отсутствие отсутствующей серии не превращалось в alert. Jenkins runtime может отсутствовать без running builds: его NoData=OK.

Last-result alerts держатся до следующего terminal result. Fail→success между poll/внутренними Jenkins updates может быть пропущен. Counter-based Jenkins failure rate — дополнительная диагностика, не гарантия уведомления о каждом запуске. Эту гарантию обеспечивают штатные build/deploy notification/event mechanisms отдельно. Не называть last-result карточки success rate за 24h.

TeamCity source native gauges могут дублироваться по nodes: для агентов/очереди использовать max. Jenkins схема рассчитана на один controller на env; не дублировать один controller ServiceMonitor. Имена Jenkins metrics namespace и job attribute фиксированы в plugin config: default_jenkins и jenkins_job. При других значениях изменить генератор/queries после проверки реального /prometheus/.

## Исключённые KPI

Octopus свободные worker slots, compatibility-aware capacity и per-target agent health; p95 сборок для TC/Octopus; единый процент всех запусков за период; полный долговременный REST history в Prometheus. Нет exporter Pods или PVC для собственного кода. Infinity JSONata expressions и offline generator — конфигурация стандартного open-source plugin, не runtime adapter.
