# CI/CD Health — план внедрения и комплект Grafana

Подготовлено 05.10.2026. Это импортируемый шаблон для Grafana OSS с Prometheus. Он не подключён к вашим серверам: адреса, токены, версии, проекты и пулы не предоставлены. Штатные панели TeamCity/Jenkins начинают работать после настройки scrape; основной обзор и алерты требуют адаптера по METRICS-CONTRACT.md. Реализация этого адаптера в комплект не входит. Dashboard JSON не устанавливает алерты: они вынесены в отдельный provisioning YAML.

## 1. Что изучено

* TeamCity dashboard **11669**: https://grafana.com/grafana/dashboards/11669-teamcity/ — сервер, агенты, running/queued builds, JVM. Основа для деталей TeamCity. Это старый шаблон для TeamCity 2019.2: проверять метрики на своей версии. Настройку TLS из чужого примера не копировать автоматически.
* Jenkins Monitoring **14550**: https://grafana.com/grafana/dashboards/14550-jenkins-monitoring/ — executors, очереди, состояния узлов, JVM и jobs. Основа группировки деталей Jenkins.
* Jenkins **12646**: https://grafana.com/grafana/dashboards/12646-jenkins/ — обзор пользовательского Prometheus dashboard.
* Grafana Jenkins datasource: https://grafana.com/docs/plugins/grafana-jenkins-datasource/latest/ — встроенные Overview/DORA dashboards. Это Enterprise datasource; для данного решения он не требуется.

Общий обзор спроектирован под ежедневную эксплуатацию: состояние систем наверху, ниже результаты/нагрузка, затем проблемные объекты, свежесть данных и алерты. Он не копирует весь JVM мониторинг в первый экран. Превью в комплекте — иллюстрация раскладки с демонстрационными числами, не скриншот работающей Grafana. Реальный JSON использует только стандартные панели и не содержит фиктивных данных.

## 2. Архитектура

TeamCity /app/metrics и Jenkins /prometheus/ → Prometheus.

TeamCity REST / Jenkins API / Octopus REST → нормализующий адаптер /metrics → Prometheus.

Blackbox Exporter → синтетические HTTP/readiness проверки → Prometheus.

Prometheus + recording rules → Grafana Dashboard и Grafana-managed Alerting → Teams / PagerDuty.

Дополнительно: завершения отдельных операций → webhook/event collector → журнал событий с run ID и URL. Для гарантированного уведомления о КАЖДОМ падении этот событийный канал важен: polling последнего результата может пропустить последовательность failed → success между двумя опросами, а metric alert может сгруппировать несколько failures одного pipeline.

Grafana — основной интерфейс и единственный владелец правил уведомлений. В Prometheus в данном комплекте находятся recording rules, а не alert rules. Не включать параллельно те же уведомления в кастомном прототипе.

## 3. Шаги внедрения

### Шаг 1 — зафиксировать scope

1. Записать версии Grafana, Prometheus, TeamCity, Jenkins core/plugin, Octopus; topology (single node/HA), base URLs, context paths и reverse proxy.
2. Согласовать критичные проекты, pipelines, deployment environments, бизнес-владельцев и команду дежурства. Начать с одного production-потока каждого типа.
3. Определить inventory ожидаемых серверов, постоянных агентов, pools, Octopus workers и deployment targets. Ephemeral агенты контролировать ёмкостью pool, а не требованием вечного существования каждого ID.
4. Определить различия: online/authorized/enabled agent; idle executor; worker; healthy deployment target. Один Jenkins node может иметь несколько executors. Deployment target Octopus не является CI агентом.
5. Согласовать baseline: успешность, длительность p95, нормальная очередь, допустимое ожидание, нужная совместимая ёмкость по pool/labels, окна обслуживания.

Результат: таблица источников, inventory, scope и утверждённые значения thresholds.

### Шаг 2 — подготовить доступ

1. Выделить сервисные учётные записи с минимальными правами чтения. TeamCity для метрик требует View usage statistics; для REST добавить нужные права просмотра builds/agents/projects.
2. Jenkins: установить совместимую версию Prometheus Plugin; при authentication endpoint требуются соответствующие права Metrics.VIEW. API token хранить отдельно от dashboard.
3. Octopus: отдельный API key на чтение нужных Spaces, deployments, tasks, workers и targets. Использовать заголовок X-Octopus-ApiKey.
4. Доступ из Prometheus/adapter/Grafana к источникам; из Grafana к Teams/PagerDuty. Внутренний CA добавить в trust store или tls_config.ca_file. Токены хранить в secrets, не в JSON.

### Шаг 3 — подключить штатные метрики

1. TeamCity: проверить GET /app/metrics с сервисной авторизацией. В Diagnostics → Metrics посмотреть фактические названия, labels и experimental metrics.
2. Jenkins: endpoint по умолчанию /prometheus/ со завершающим slash. Согласовать namespace; детали JSON рассчитаны на default_jenkins. Включить node status и нужные build metrics.
3. У Jenkins collection period может быть 120s по умолчанию. Scrape 30s не превращает такие данные в свежие каждые 30s. Согласовать collection period 30–60s после оценки нагрузки или поднять freshness thresholds.
4. Добавить jobs из prometheus-scrape.example.yml в существующую конфигурацию. Не заменять весь текущий prometheus.yml этим фрагментом.
5. Проверить targets, up, правильность labels env/component, отсутствие дубликатов при HA. В TeamCity max/sum зависят от того, является метрика общей или локальной для node.

Результат: штатные metrics доступны, нижние details-панели проверены по фактическому endpoint.

### Шаг 4 — добавить результаты и нормализацию

Штатные endpoint не считать универсальным источником всех KPI. Реализовать адаптер с контрактом METRICS-CONTRACT.md:

* TeamCity: GET /app/rest/builds, /app/rest/buildQueue, /app/rest/agents; получить статусы, queued/start/finish times, enabled/authorized/connected и состояние running builds. Использовать JSON, fields и locator с учётом версии.
* Jenkins: сопоставить plugin counters/queue/executors, а API применять для недостающего inventory, runnable/blocked очереди и running elapsed. Boolean last_build_result считает UNSTABLE успешным; для строгого success rate его напрямую использовать нельзя.
* Octopus: GET /api/{spaceId}/deployments, /tasks, /machines и endpoints worker pools вашей версии. Deployment связывать с task по TaskId; статус/StartTime/CompletedTime брать из task. Считать очередь deployment tasks отдельно от прочих служебных задач. Pending manual intervention отображать отдельной причиной ожидания и не считать автоматическим отказом worker.
* Pagination обязательна: прочитать все необходимые страницы, а не первые 30 записей. Схемы ответов/параметры сверять с /swaggerui/ вашей установки. В частности, machines в разных версиях могут иметь разную обёртку ответа.
* Сохранять cursor обработанных terminal events, counters и гистограммы в БД/диске. Дедупликация: component + source-instance + run/task ID. События приходят повторно и могут опаздывать.
* На каждом опросе отдельно обновлять current gauges; counters/histogram обновлять ровно один раз на завершение. Во время первой синхронизации не выдавать старые ошибки за новые. Начать с выбранного watermark; исторические данные хранить отдельно.
* До полной успешной загрузки inventory и обязательных KPI выставлять ci_collection_complete=0. На ошибке не обновлять timestamp успешного полного сбора. API HTTP=200 со страницей login не считать успешным чтением API.

Результат: все ci_* метрики имеют согласованную семантику, не теряют короткие завершившиеся операции и корректно переживают рестарт.

### Шаг 5 — Octopus через Infinity, если нужен быстрый первый этап

Возможна альтернатива адаптеру: установить Infinity, настроить allowed hosts и API key в secure datasource settings, выбрать JSON + backend JSONata/JQ. API endpoints и pagination сверить на своей версии. Получать deployments и связанные tasks, таблицу последних деплоев, queue и targets.

Backend parsers поддерживают alerting; frontend/UQL для этого не подходят. Infinity сам по себе не превращает текущее API состояние в хранимую временную историю для p95/24h trends. Для большого набора проектов, единой модели и восстановления после пропусков рекомендуемый вариант — адаптер/exporter. Поставляемый основной JSON рассчитан на Prometheus + адаптер; Infinity-вариант требует отдельных queries/panels и не включён в JSON.

### Шаг 6 — HTTP и server readiness

1. Настроить Blackbox Exporter из blackbox.example.yml. TeamCity проверять /healthCheck/ready, а не только /healthy: живой процесс может быть ещё не готов к работе.
2. Jenkins /login и Octopus / — только внешний web-check. Login page и redirect не подтверждают работоспособность backend; поэтому комбинировать их с authenticated ci_api_up.
3. Согласовать expected HTTP status, redirect policy и место проверки. Время probe включает network/TLS, это не latency отдельной API операции.
4. При необходимости добавить Node/Windows Exporter: CPU/RAM/disk/JVM/GC/DB в component details. Первый operational screen оставлять компактным.

### Шаг 7 — recording rules и dashboard

1. В prometheus-recording-rules.yml поменять статический expected inventory: prod + teamcity/jenkins/octopus. Добавить остальные env, если они нужны. Для нескольких source instances агрегировать в адаптере осознанно; этот starter рассчитан на один логический source каждого компонента.
2. Проверить `promtool check config /etc/prometheus/prometheus.yml` и `promtool check rules /etc/prometheus/rules/prometheus-recording-rules.yml`, затем reload Prometheus. Проверить `ci:expected_component`, `ci:operational_state` в query UI.
3. Подключить Prometheus datasource, рекомендуемый UID `cicd-prometheus`; пример provisioning в комплекте. Если используется иной UID, заменить его в grafana-alert-rules.yml.
4. Grafana → Dashboards → New → Import → загрузить cicd-health-dashboard.json → после импорта выбрать источник в dropdown Prometheus (DS_PROMETHEUS). Рекомендуемая целевая версия: Grafana 12+; используются обычные text/stat/timeseries/table/bargauge/alertlist панели.
5. В Settings → Variables заменить hidden constants teamcity_url, jenkins_url, octopus_url. Заменить wiki.example.internal в dashboard links и alerts. Полезно дать runbook URL каждому классу проблем.
6. Проверить ссылки на details-панели, component фильтр, project multiselect, часовой пояс browser и now-24h. Ресурсы и HTTP метрики намеренно не фильтруются по project.
7. Основной общий статус берёт худший статус ВСЕХ ожидаемых компонентов выбранного env; component-фильтр не скрывает аварию из общей карточки.

### Шаг 8 — алерты и доставка

1. Grafana-managed rules из grafana-alert-rules.yml установить в /etc/grafana/provisioning/alerting/ после настройки UID datasource, orgId, folder и thresholds. В Grafana Cloud применять поддерживаемый API/Terraform/UI, а не локальный filesystem provisioning.
2. В Teams создать Workflow «Post to a channel when a webhook request is received», получить URL, добавить Microsoft Teams contact point Grafana, выполнить Test и проверить историю workflow. Назначить владельцев workflow, чтобы уход одного сотрудника не отключал канал.
3. В PagerDuty создать service/integration и contact point с integration key. Проверить firing и resolve. Определить escalation policy и расписание on-call.
4. Notification policies: domain=cicd; warning → Teams; critical → Teams + PagerDuty. Не эскалировать каждое падение dev/feature ветки: scope и severity адаптировать под критичность pipeline. Стартовый комплект ставит deploy failures critical, build failures warning.
5. Рекомендованные начальные настройки: group_by=[alertname,env,component,project,pipeline]; group_wait=15s; group_interval=2m; repeat_interval=4h. Для outage группировать на уровне сервиса.
6. Mute timings — окна обслуживания. Недоступность сервера подавляет зависимые KPI alerts через available/fresh фильтры в queries. В Grafana отдельно маршрутизировать DatasourceError alerts; при недоступности Prometheus/Grafana должен сработать независимый watchdog из другой системы.
7. noDataState=OK в шаблоне применён к разреженным event/resource сериям: отсутствие ошибок/запусков нормально. Потерю обязательного сбора контролируют expected inventory, freshness/complete и отдельное правило missing recording rules. Collector обязан считать отсутствие обязательного KPI неполным сбором.
8. Grafana rule error state=Error. Ошибки queries нельзя считать успешным состоянием CI/CD. Настроить доставку этих error notifications и проверить её отдельно.

Начальные thresholds — значения для запуска обсуждения, не универсальный SLA:

| Условие | Порог / удержание | Severity |
|---|---|---|
| HTTP/API недоступны | 2m | critical |
| Свежесть/полнота | >180s или incomplete, 3m | warning |
| Failed/timed_out build | новое событие в 5m, без for | warning |
| Failed/timed_out deployment | новое событие в 5m, без for | critical |
| Expected agent/worker/target offline | 5m | warning |
| Нет свободной совместимой ёмкости и очередь >0 | 5m | warning |
| Размер очереди выше ci_queue_limit | 10m | warning |
| Старейшее задание ждёт >900s | 5m | critical |
| Сборка дольше ci_operation_timeout_seconds | 5m | warning |
| HTTP probe дольше 2s | 5m | warning |
| Штатный scrape неуспешен | 3m | warning |
| Expected recording series пропали | 2m | critical |

Не устанавливать for=5m для разового события падения: за время удержания окно события может закончиться. `increase(counter[5m])` показывает наблюдаемые изменения, но не заменяет per-run event notifications. В metric alert нет гарантии отдельного сообщения для каждого run.

### Шаг 9 — приёмка

* Контрольная failed build; failed deployment; canceled/unstable/timed_out результаты. Failed → success между polls также должен быть замечен event collector.
* Отключить постоянный агент; проверить исключение maintenance/disabled и ожидаемое поведение autoscaling pool.
* Искусственно занять executors, поставить несовместимую по labels задачу, создать большую/старую очередь.
* Превысить лимит сборки, затем завершить её; проверить resolve и отсутствие старого running gauge.
* Отключить TeamCity/Jenkins/Octopus, adapter, Blackbox, штатный scrape, затем сами recording rules. Проверить UNKNOWN при исчезновении наблюдений и DOWN при наблюдаемом отказе; зелёный статус при полном отсутствии данных недопустим.
* Проверить пагинацию >1 страницы, timezone, рестарт адаптера, дедупликацию, reset counters, отсутствие исторической волны alerts при первом включении.
* Проверить panel values по UI источников на выбранной выборке; сверить HTTP endpoint и времена.
* Подтвердить реальную доставку Teams/PagerDuty и resolve с on-call человеком; доставка ещё не настроена этим пакетом.

### Шаг 10 — завершить внедрение

1. В течение 1–2 недель вести новый мониторинг параллельно с прототипом и ежедневно разбирать расхождения/шум.
2. Утвердить dashboard как единственную operational точку входа, назначить владельцев collector/rules/runbooks, хранить конфигурацию в Git.
3. Обучить команду: карточка → детали → источник → runbook. Зафиксировать порядок действий и обновления thresholds.
4. После приёмки отключить alerts и сбор в прототипе, отозвать лишние tokens, архивировать код/данные. Если нужна большая панель, использовать Grafana kiosk/playlist; отдельный read-only display без собственного alert engine допустим.
5. Периодически проверять интеграции, renew сертификатов, работоспособность workflow и тестовые notifications; после обновлений CI/CD сверять контракт метрик.

Ориентир для оценки: 1–2 дня inventory/access, 1–2 дня native collection, 3–5 дней adapter/event handling, 1–2 дня dashboard/alerts, 2–3 дня приёмки плюс период параллельного наблюдения. Это приблизительная инженерная оценка; HA, сетевые доступы и согласования могут существенно изменить срок.

## 4. Что отображает JSON

33 верхнеуровневых элемента, включая разделители, и 12 вложенных detail-панелей. Три component cards + общий operational state; success rates builds/deploys; failures; CI agents online; queue; duration p95; trends; свободная ёмкость; HTTP latency; последние результаты и их возраст; свежесть полного сбора; ресурсный inventory; running elapsed/limit; список Grafana alerts; сворачиваемые details TeamCity/Jenkins/Octopus.

HEALTHY — не доказательство того, что конкретный релиз прошёл всю цепочку. Для такой корреляции нужен общий release/artifact/commit ID между CI и CD и отдельный event journal/trace view. Компоненты не обязательно образуют последовательность TeamCity → Jenkins → Octopus; связь должна соответствовать вашей реальной схеме pipeline.

## 5. Формулы и ограничения

* Success rate = success / (success + failed + unstable + timed_out) за выбранный диапазон. Canceled исключён; при необходимости включить его отдельной метрикой. Нет операций → NO DATA, не 100%.
* p95 рассчитывается из histogram buckets, не из average и не из усреднения квантилей разных источников.
* Последний результат — на каждый pipeline; он не означает долю успешных сборок за сутки. Возраст результата показывается отдельно от возраста опроса.
* Operational state: DOWN при наблюдаемом HTTP/API отказе; UNKNOWN при пропавших/неполных/устаревших наблюдениях; DEGRADED при очереди >300s, нулевой ёмкости с очередью, expected offline resource или failed/timed_out за 15m; иначе HEALTHY.
* DEGRADED из-за события держится около 15 минут; это окно индикации, не открытый бизнес-инцидент. Неуспешный последний deployment по проекту остаётся виден в таблице и требует оценки владельца.
* CI/CD overall — худший status по expected inventory, а не среднее up. При нескольких environments одно значение выбирается env variable.
* Online agents, executors и deployment targets не складывать в одно число. Верхняя agents-карточка относится только к TeamCity/Jenkins.
* HTTP availability — доля успешных измерений, а не готовый contractual SLA. Для SLA учитывайте пропущенные измерения и плановые окна отдельно.
* Исторические KPI появятся после накопления данных. Экстраполяция `increase()` может давать дробные counts на границе диапазона; точные per-run количества брать из event store.

## 6. Проверка подготовленных файлов

JSON и YAML проверены на читаемость и внутренние структурные связи; проверены уникальность panel IDs, links на details, непересекающаяся раскладка раскрытого основного экрана, и совпадение названий метрик с контрактом. Grafana/Prometheus в этой среде не запущены; promtool и runtime import/evaluation не выполнялись. Импорт, PromQL evaluation, plugin field schema и реальные значения обязательно проверить при шаге 7/приёмке.

## 7. Документация

* TeamCity metrics/readiness: https://www.jetbrains.com/help/teamcity/teamcity-monitoring-and-diagnostics.html
* TeamCity builds: https://www.jetbrains.com/help/teamcity/rest/buildapi.html
* TeamCity queue: https://www.jetbrains.com/help/teamcity/rest/buildqueueapi.html
* Jenkins plugin: https://plugins.jenkins.io/prometheus/
* Jenkins metrics: https://github.com/jenkinsci/prometheus-plugin/blob/master/docs/metrics/index.md
* Octopus API: https://octopus.com/docs/api ; deployments/tasks: https://octopus.com/docs/api/deployments ; https://octopus.com/docs/api/tasks
* Infinity query/alerting: https://grafana.com/docs/plugins/yesoreyeram-infinity-datasource/latest/query/
* Teams workflow: https://grafana.com/docs/grafana/latest/alerting/configure-notifications/manage-contact-points/integrations/configure-teams/
* Grafana alert provisioning: https://grafana.com/docs/grafana/latest/alerting/set-up/provision-alerting-resources/
