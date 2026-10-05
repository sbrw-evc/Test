# Контракт адаптера — пользовательские ci_* метрики

Эти имена НЕ являются встроенными метриками TeamCity, Jenkins или Octopus. Это обязательная спецификация адаптера, реализацию которого нужно добавить при внедрении. Один логический источник на component/env; active/passive реплики не должны одновременно экспортировать те же counters. Все длительности в секундах, timestamps — Unix seconds UTC. Label env обозначает окружение мониторинга (пример prod). Deployment environment включать в стабильный pipeline ID или добавить отдельный label с доработкой queries/alerts.

## Component, pool, resource gauges

| Имя | Дополнительные labels к env,component | Семантика |
|---|---|---|
| ci_api_up | — | 1: authenticated read API успешно и схема проверена; 0: отказ. На частичной ошибке API может быть up, но complete=0 |
| ci_collection_complete | — | 1: все обязательные метрики/inventory получены; 0: неполный цикл/ошибка/необработанная pagination |
| ci_collection_last_success_timestamp_seconds | — | Время последнего успешного полного цикла; на ошибке не обновлять |
| ci_agents_online | pool | Число online enabled authorized CI agents; только TeamCity/Jenkins, не executors |
| ci_capacity_free | pool | Свободные совместимые слоты: TC agents / Jenkins executors / Octopus worker slots |
| ci_queue_length | pool | Полная ожидающая очередь на pool, включая несовместимые/blocked по согласованной модели; не выполнять двойной счёт задачи между pools |
| ci_queue_oldest_age_seconds | pool | now - queued_at старейшей задачи; 0 при пустой очереди |
| ci_queue_limit | pool | Согласованный threshold размера очереди (стартовый пример 10), не ёмкость сервера |
| ci_resource_online | resource_id,resource_kind,pool | 0/1, только ожидаемые non-ephemeral resources; resource_kind agent/worker/target. Исчезнувший ожидаемый resource остаётся со значением 0. Disabled/maintenance исключаются по конфигурации |

Pool должен быть стабильным и одинаковым в queue/capacity/limit series. Для queue, которую могут обработать несколько pools, выбрать один логический eligibility pool либо отдельный dispatcher; простое дублирование ломает sums. Idle executors с неподходящими labels не являются свободной совместимой ёмкостью. Для autoscaling проверять minimum desired capacity и pending provisioning отдельно.

ci_api_up/complete/timestamp экспортировать и на пустой системе; gauges pool также публиковать с 0. Pool inventory должен быть получен полностью. Если необходимой метрики нет, complete=0, а не выдуманное значение 0. Все отсутствующие counter status-series должны быть заранее созданы с нулём.

## Operations

Общие labels: env,component,project,pipeline,operation. Project и pipeline — стабильные IDs/имена; operation=build/deploy. Pipeline включает реальный deployment environment, если это нужно для разделения. Между TC/Jenkins/Octopus projects заранее определить mapping; фильтр project должен иметь единую бизнес-семантику.

| Имя | Дополнительные labels | Тип / значение |
|---|---|---|
| ci_operations_total | status | Counter завершённых операций. status success/failed/unstable/canceled/timed_out. Все пять series существуют даже при нуле |
| ci_operation_duration_seconds | histogram: le | Histogram длительности start→finish, один observe на terminal event. В /metrics нужны _bucket, _sum, _count |
| ci_last_operation_status | — | Gauge последней завершённой операции: 0 success, 1 unstable, 2 failed, 3 canceled, 4 timed_out |
| ci_last_operation_completed_timestamp_seconds | — | Unix timestamp последнего завершения; до первой операции series отсутствует |
| ci_running_elapsed_seconds | — | Gauge максимального elapsed среди одновременно выполняющихся операций этого pipeline; series удалить после завершения всех |
| ci_operation_timeout_seconds | — | Индивидуальный threshold выполнения; labels точно совпадают с running elapsed. Передавать series только для активных pipelines либо постоянные thresholds |

Buckets пример: 30,60,120,300,600,900,1800,3600,7200,+Inf. Одинаковые buckets на всех exporters, иначе объединённый p95 некорректен. Отменённые операции можно учитывать в duration histogram, если это согласовано; документировать этот выбор.

Не добавлять run ID, commit, arbitrary branch, task URL в labels долгоживущих counters/histograms. Такой event context хранить в event store/logs. Для гарантированных per-run Teams/PagerDuty уведомлений использовать events с дедупликацией и отдельным routing; metric alert сообщает о наличии failures pipeline в окне, а не отправляет гарантированно каждый run.

Не вычислять counter путём пересчёта первых N API результатов на каждом опросе. Нужны persistent watermark, pagination, overlap window для запоздавших событий и dedup. Инициализация counters до первой failure нужна, иначе первая scrape уже с counter=1 может не дать наблюдаемого increase.

## Сопоставление штатных источников

| Наш KPI | Источник / замечание |
|---|---|
| TC agents | agents_connected_authorized_number — только connected/authorized; enabled/idle и per-resource API могут требовать REST |
| TC queue/running | builds_queued_number/builds_running_number + REST timestamps/status |
| Jenkins free slots | default_jenkins_executors_idle; проверить совместимость labels/pool |
| Jenkins runnable queue | default_jenkins_executors_queue_length — не считать автоматически полной blocked queue |
| Jenkins strict success | ordinal=0, а не boolean last_build_result=1 (тот включает UNSTABLE). Counters plugin можно сопоставить при согласованной семантике retention/restarts |
| Jenkins duration | *_milliseconds → seconds делением на 1000. Summary нельзя превратить в histogram записью quantile; получить individual duration events |
| Octopus deployment | deployment.TaskId → task.State + StartTime/CompletedTime; получить все страницы |
| Octopus targets | machines HealthStatus и disabled/maintenance; отдельно от worker capacity |
| API доступность | Реальный authenticated GET + валидация схемы ответа, не login page |

Штатные metrics нужны для server health/details; normalized events нужны для одинаковых KPI. Включение per-build Jenkins metrics с номером запуска не отменяет необходимость контроля cardinality и истории.

## Пример строк (только иллюстрация формата)

```text
ci_api_up{env="prod",component="teamcity"} 1
ci_collection_complete{env="prod",component="teamcity"} 1
ci_collection_last_success_timestamp_seconds{env="prod",component="teamcity"} 1791200000
ci_capacity_free{env="prod",component="teamcity",pool="linux"} 3
ci_queue_length{env="prod",component="teamcity",pool="linux"} 2
ci_queue_limit{env="prod",component="teamcity",pool="linux"} 10
ci_queue_oldest_age_seconds{env="prod",component="teamcity",pool="linux"} 45
ci_operations_total{env="prod",component="teamcity",project="billing",pipeline="billing-main",operation="build",status="success"} 120
```

Не scrape этот пример вместо источников. Dashboard не содержит sample metric data.
