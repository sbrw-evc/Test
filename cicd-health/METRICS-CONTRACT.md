# Контракт snapshot адаптера версии 1

Реализация: `adapter/exporter.py`. Все `ci_*` — Gauges. Нет counters и histogram. Scrape каждые 30 s, polling по source в отдельных threads каждые 30 s после завершения предыдущего poll. Labels `env`, `component` обязательны. `component`: teamcity / jenkins / octopus, ровно по одному экземпляру источника.

| Метрика | Дополнительные labels | Значение |
|---|---|---|
| ci_api_up | — | 1 после успешных чтений API; 0 при HTTP/auth/network failure. При ошибке схемы/лимита страниц после ответа API остаётся 1 |
| ci_collection_complete | — | 1 только если собран весь настроенный scope |
| ci_collection_last_success_timestamp_seconds | — | Unix seconds последнего полностью успешного poll; 0 до первого полного poll |
| ci_queue_length | — | Очередь только настроенных pipeline scopes |
| ci_queue_oldest_age_seconds | — | Максимальное ожидание queued task/build; 0 при пустой очереди |
| ci_queue_limit | — | Порог из config.yml |
| ci_agents_online | — | Только TC/Jenkins; не свободные слоты |
| ci_agents_minimum | — | Порог online count TC/Jenkins |
| ci_resource_online | resource_id | Только явно заданные expected_agents TC/Jenkins; отсутствующий ID = 0 |
| ci_last_operation_status | project, pipeline, operation | 0 success, 1 unstable, 2 failed, 3 canceled, 4 timed out, 5 unknown/not-built |
| ci_last_operation_completed_timestamp_seconds | project, pipeline, operation | Unix seconds завершения выбранного terminal result |
| ci_last_operation_duration_seconds | project, pipeline, operation | Start→finish в секундах; отсутствует, если StartTime неизвестен |
| ci_running_elapsed_seconds | project, pipeline, operation | Максимальный возраст активного запуска, 0 если активных нет |
| ci_operation_timeout_seconds | project, pipeline, operation | Порог длительности из config |

`operation`: build для TC/Jenkins, deploy для Octopus. `env` — контур мониторинга; отдельные deployment environments описываются отдельными scope/pipeline labels.

**Статусы и частота.** Это снимок, а не журнал каждого события. TC выбирает newest terminal build, Jenkins lastCompletedBuild, Octopus newest-created terminal Deploy task с project/environment фильтрами. Octopus не выбирает max CompletedTime; concurrent tasks могут завершаться в другом порядке. Failure gauge остаётся до следующего terminal result. Fail→success между опросами может не быть замечен. Доля успеха — доля выбранных pipeline с успешным последним результатом; без run history она не является success rate за период. Pipeline без completed runs не публикует last_*; нельзя считать его успешным.

**Scope агентов.** TC: connected AND authorized AND enabled на всём доступном token scope. Jenkins: offline=false и numExecutors>0, controller displayName Built-In Node/master исключён по умолчанию; для нестандартного имени контроллера задать agent_names явно. enabled TC/Jenkins agents могут быть заняты или несовместимы с ожидающим job. Minimum count не заменяет capacity alert. Expected stable IDs отсутствующие в API считаются offline; исключить динамические agent Pods и проверять права token.

**Ошибки.** Poll публикуется атомарно по компоненту. При ошибке сохраняется предыдущий snapshot, complete=0, last_success не меняется. Dashboard KPI фильтруются через fresh+available recordings. Missing probe/exporter или данные старше180 s → UNKNOWN. Наблюдаемая HTTP/API ошибка → DOWN, в том числе 403/404, которые могут означать права/конфигурацию, а не отказ сервера. На /metrics всегда 200 даже при source failure.

**Пагинация.** TC follows same-origin nextHref. Octopus active tasks skip/take100 до TotalResults; filter Deploy/project/environment. Jenkins active scan обходит retained builds по tree ranges100. max_pages по умолчанию50; превышение не превращается в частичный успешный snapshot. Частота запросов и ограничения оцениваются DevOps. Пагинация API не является транзакционным снимком при concurrent changes.

**Код безопасности.** GET only, credentials из env Secret, TLS verified, optional CA file. Redirects и cross-origin pagination запрещены. Logs не выводят URLs/токены. Kubernetes Pod не нуждается в API token, RBAC, PVC или write filesystem.

**Исключены:** ci_operations_total, histogram, p95, free compatible capacity, Octopus workers/targets/agents, webhook event delivery. Не добавлять recording/alert queries к ним до появления проверенного источника.
