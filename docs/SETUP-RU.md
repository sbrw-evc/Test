# Запрос в DevOps — настройка мониторинга CI/CD в Kubernetes

Просим подключить TeamCity, Jenkins и Octopus к существующим Prometheus и Grafana. Собственный exporter не нужен. Используем штатный `/app/metrics`, open-source Jenkins Prometheus Plugin, `prometheus/blackbox_exporter` и `grafana/grafana-infinity-datasource` с серверным JSONata для REST API. Выбор готовых решений и ссылки: [OSS-SOURCES.md](OSS-SOURCES.md).

Сначала подключить проверочный scope: один TeamCity buildType, выбранные Jenkins jobs и один Octopus project/environment. Затем перечислить все критичные pipelines и повторить REST queries/alerts для каждого scope с отдельными UID. **Пример сам по себе не контролирует все проекты организации.**

## 1. Заполнить параметры

Все команды выполнять из корня репозитория. Не отправлять реальные токены в Git.

```bash
python3 -m venv .venv
.venv/bin/pip install -r scripts/requirements.txt
cp monitoring-config.example.yml monitoring-config.yml
# Заполнить monitoring-config.yml фактическими URL, IDs, фильтрами и порогами.
.venv/bin/python scripts/generate_monitoring.py monitoring-config.yml
```

Генератор создаёт конфигурации Grafana и Prometheus; он не запускается в кластере и не собирает данные.

| Параметр | Что указать |
|---|---|
| `env` | Контур, например prod. Использовать то же значение в labels Service и Probe. |
| `teamcity_url`, `jenkins_url`, `octopus_url` | Доступные из кластера HTTPS URL с правильным context path. |
| `teamcity_build_type` | ID одной конфигурации сборки, не display name. |
| `jenkins_job_regex` | Regex полного job name; в плагине label имени job должен быть `jenkins_job`. |
| `jenkins_node_regex` | Агенты, без controller. |
| `jenkins_capacity_label` | Одна Jenkins label для executors/queue. Не суммировать пересекающиеся labels. |
| `octopus_space`, `octopus_project`, `octopus_environment` | IDs вида Spaces-1, Projects-1, Environments-1. |
| `*_expected_agent`, `jenkins_expected_node` | Только постоянные агенты. Ephemeral Kubernetes agents не считать обязанными всегда существовать. |
| `*_min_agents`, `*_queue_limit`, `*_timeout_seconds` | Согласованные пороги. Начальные значения — пример, не SLA. |

Если постоянного ожидаемого агента нет, убрать его panel/query и правила `oss-tc-agent` либо `oss-jenkins-agent`/`oss-jenkins-node-missing` до применения. Сохранить алерт на общее количество online agents.

## 2. Выдать доступ и проверить источники

Создать сервисные аккаунты с доступом на чтение. Получить фактические права по установленным версиям систем и проверить ответы, а не только наличие токена.

- **TeamCity:** чтение выбранного проекта, builds и agents; для `/app/metrics` — `View usage statistics`. Проверить `/app/metrics`, `/app/rest/builds` и `/app/rest/agents` с токеном и `Accept: application/json`.
- **Jenkins:** установить [Prometheus Plugin](https://plugins.jenkins.io/prometheus/). Проверить `/prometheus/` (при context path добавить префикс). Сохранить стандартный prefix метрик `default_jenkins`, включить сбор build metrics, задать job attribute `jenkins_job`. Проверить фактический внутренний collection interval; scrape 30s не ускоряет внутренний collector. При отличиях имён исправить generator и [контракт](../cicd-health/METRICS-CONTRACT.md).
- **Octopus:** API key с чтением выбранных Space, projects, environments и deployment tasks. Проверить `/api/Spaces-1/tasks?name=Deploy&project=Projects-1&environment=Environments-1&take=1` по API установленной версии. Ожидаются `TotalResults` и `Items`.

Хранить токены в принятой в компании системе секретов. Для native scrapes нужен Kubernetes Secret `cicd-native-credentials` в namespace ServiceMonitor с ключами `TEAMCITY_TOKEN`, `JENKINS_USER`, `JENKINS_TOKEN`. Для Grafana передать через `secretKeyRef` переменные `TEAMCITY_TOKEN` и `OCTOPUS_API_KEY`. Не включать anonymous доступ к credentials или source endpoints.

Разрешить сеть: Prometheus → TeamCity/Jenkins/blackbox; blackbox → три HTTP endpoints; Grafana → TeamCity/Octopus API, Prometheus и получатели уведомлений. DNS и NetworkPolicy должны разрешать эти потоки.

## 3. Подключить штатные метрики в Prometheus

Комплект рассчитан на существующий Prometheus Operator в Kubernetes. Сбор подключается через ServiceMonitor и Probe, правила — через PrometheusRule.

1. В [native-metrics.example.yml](../kubernetes/native-metrics.example.yml) заменить namespaces, selector labels, `release`, named Service port, scheme, paths и TLS serverName.
2. На Services TeamCity/Jenkins установить labels `monitoring-source=teamcity|jenkins`, `component=teamcity|jenkins`, `env=<контур>`. У Service должны быть соответствующие Endpoints.
3. Для собственного CA создать ConfigMap `cicd-ca` с `ca.crt` в namespace ServiceMonitor. Для публичного CA удалить ненужную ссылку `tlsConfig.ca`, сохранив проверку сертификата. При plain HTTP внутри mesh явно согласовать scheme/port и убрать неуместный tlsConfig; не отключать TLS verify для HTTPS.
4. Сверить `serviceMonitorSelector`, `serviceMonitorNamespaceSelector`, `probeSelector`, `probeNamespaceSelector` и `ruleSelector` Prometheus CR с labels/namespace файлов. Примеры `release=kube-prometheus-stack` могут отличаться от вашего Helm release.
5. Проверить и применить:

```bash
kubectl apply --dry-run=server -f kubernetes/native-metrics.example.yml
kubectl apply -f kubernetes/native-metrics.example.yml
```

В Prometheus Targets должны появиться `job="teamcity"` и `job="jenkins"`, оба `up=1`. Проверить наличие: `agents_connected_authorized_number`, `builds_queued_number`, `default_jenkins_nodes_online`, `default_jenkins_executors_queue_length`, `default_jenkins_builds_last_build_result_ordinal`, `default_jenkins_builds_running_build_duration_milliseconds`. Последняя может отсутствовать при отсутствии running builds; подтвердить её на тестовой выполняющейся сборке. Полный список и единицы — в контракте метрик.

## 4. Настроить проверку доступности

1. Установить либо использовать уже работающий [prometheus/blackbox_exporter](https://github.com/prometheus/blackbox_exporter) через принятый Helm/GitOps процесс. Зафиксировать одобренную версию/image digest. Свой образ собирать не требуется.
2. Загрузить модуль `http_2xx` из [blackbox.example.yml](../cicd-health/blackbox.example.yml): HTTP 2xx, timeout 10s, TLS verify включён. При частном CA смонтировать сертификат в blackbox Pod и указать `ca_file` в модуле.
3. В [probes.example.yml](../kubernetes/probes.example.yml) заменить адрес Service blackbox, URL серверов, `env`, namespace и release label. Сохранить `jobName: cicd-http`, interval 30s и scrapeTimeout 15s.
4. TeamCity проверять через `/healthCheck/ready`. Jenkins `/login` и Octopus `/` проверяют HTTP frontend; полноценный доступ к API проверяется отдельно native scrape/Infinity. Проверить, что reverse proxy не отдаёт 200 при недоступном backend.
5. Применить Probe и recording rules:

```bash
kubectl apply --dry-run=server -f kubernetes/probes.example.yml
kubectl apply -f kubernetes/probes.example.yml
kubectl apply --dry-run=server -f kubernetes/recording-rules.yml
kubectl apply -f kubernetes/recording-rules.yml
```

В Prometheus проверить `probe_success{job="cicd-http"}`, `probe_duration_seconds{job="cicd-http"}`, `ci:expected_component`, `ci:infrastructure_state` и `ci:observation_missing`. Должны присутствовать три component. Проверить Prometheus Rules на ошибки. Для TeamCity HA используется `max`, а не сумма дублируемых node metrics.

## 5. Подключить Grafana и импортировать dashboard

1. Установить официальный [Infinity datasource](https://grafana.com/grafana/plugins/yesoreyeram-infinity-datasource/) в Grafana; закрепить проверенную версию. Для alerting обязательно использовать backend parser **JSONata**, в JSON model это `parser: backend`.
2. В [grafana-datasource.example.yml](../cicd-health/grafana-datasource.example.yml) заменить Prometheus URL, TeamCity/Octopus URL и `allowedHosts` полными соответствующими origins. Сохранить UIDs: `cicd-prometheus`, `teamcity-api`, `octopus-api`.
3. Подключить файл в provisioning `datasources` через ваш Helm/GitOps процесс. Токены берутся из Grafana Pod env и сохраняются как `secureJsonData`. Для частного CA настроить доверие в Pod либо Infinity `tlsAuthWithCACert` и `secureJsonData.tlsCACert`; оставить `tlsSkipVerify=false`.
4. Проверить каждую из 13 моделей из [infinity-queries.json](../cicd-health/infinity-queries.json) в Explore. Должна получаться таблица с **одной числовой колонкой**, не строка JSON. Ошибки схемы или переполнение running-page должны приводить к ошибке запроса.
5. Импортировать [cicd-health-dashboard.json](../cicd-health/cicd-health-dashboard.json). Это 26 панелей: health cards, последние результаты, агенты, очереди, длительности, HTTP latency и детали. Верхние cards ведут к деталям и в соответствующую систему. Пустой scope отображает NO RUNS/UNKNOWN.
6. Подключить [grafana-alert-rules.yml](../cicd-health/grafana-alert-rules.yml) к provisioning `alerting`; дождаться reload/restart Grafana по вашей процедуре. Файл содержит 18 правил и удаление UID старых кастомных правил этого комплекта. Проверить orgId=1 либо заменить на вашу организацию. Не запускать одновременно старые и новые правила.

Dashboard и alerts имеют фиксированный scope, а не переменные, влияющие только на UI. При расширении scope создать отдельные запросы и alert UIDs; проверить alert cardinality для каждого job.

## 6. Настроить Teams и PagerDuty

В Grafana Alerting → Contact points создать Teams Workflow webhook по актуальной инструкции Grafana/Microsoft и PagerDuty integration с Events API integration key. Секреты передать через установленный механизм управления credentials.

Создать notification policy для `domain=cicd`: warning → Teams; critical → contact point с интеграциями Teams **и** PagerDuty. Проверить доставку Test notification в обе системы. Настроить группировку по `alertname, env`, интервал повторения и escalation с дежурной командой. Отдельно проверить маршрутизацию `DatasourceError`/`DatasourceNoData`: они должны доходить до владельцев CI/CD, даже если автоматически созданный alert не наследует ожидаемые custom labels.

| Событие | Условие в комплекте | Выдержка |
|---|---|---|
| Сервер недоступен | HTTP probe или native scrape failed | 2m |
| Пропали наблюдения | Нет ожидаемой probe/native KPI series | 3m |
| Сборка упала | Последний TC FAILURE / Jenkins ordinal 2 | Без выдержки |
| Deploy упал | Последняя выбранная terminal task Failed/TimedOut | Без выдержки |
| Octopus API недоступно | Error/NoData или неверный live response | 2m |
| Постоянный агент недоступен | TC disconnected/unauthorized/disabled/missing; Jenkins offline/missing | 5m |
| Мало агентов | Ниже настроенного минимума | 5m |
| Очередь переполнена | Выше настроенного лимита | 10m |
| Сборка выполняется долго | Возраст текущей сборки выше timeout | 5m |
| HTTP отвечает медленно | probe_duration_seconds > 2s | 5m |
| Recording rules отсутствуют | Нет expected_component | 2m |

Пороги и severity failure rules редактируются до включения production notifications. Для обязательных KPI Error/NoData не считается успехом. Длительность Jenkins без running builds допускает NoData; это оговорённое исключение.

## 7. Проверить и передать в эксплуатацию

Проверки выполнять в тестовом scope с согласованными тестовыми сбоями. DevOps передаёт ссылки на dashboard, Targets/Rules и результаты следующих проверок:

- Успешная и неуспешная сборка TC/Jenkins, успешный и неуспешный deploy Octopus дают правильные статусы. UNSTABLE и ABORTED Jenkins не превращаются в FAILURE.
- Постоянный агент offline и его исчезновение дают уведомление; штатное завершение ephemeral Pod не вызывает false positive.
- Длительная сборка и очередь выше временно сниженного тестового порога вызывают alert, после восстановления — resolved.
- Заблокированный HTTP endpoint, неверный API token и удалённая series не дают зелёный health. Для endpoint failure приходит critical notification.
- Закрыть dashboard в браузере: Infinity alerts должны продолжать выполняться на сервере Grafana и отправлять Teams/PagerDuty.
- Снять искусственные сбои и вернуть production пороги. Убедиться, что resolved notifications доставляются.

**Ограничения:** polling последних результатов может пропустить fail→success между опросами. Если нужно уведомление о каждом событии, дополнительно включить штатные CI/CD event notifications; данный комплект не гарантирует доставку каждого build/deploy event. Jenkins имеет внутренний async collector, и HTTP scrape не доказывает свежесть его job data. REST snapshots не создают исторические Prometheus counters; общий процент успехов всех систем и p95 build duration не выдумываются. Octopus worker capacity и queue waiting age исключены. Running REST queries поддерживают до 100 задач в scope, дальше намеренно возвращают ошибку вместо неполных данных.

После приёмки назначить владельца dashboard и alert runbooks, закрепить Grafana как основной operational инструмент. Кастомный прототип остановить либо оставить как экран, использующий те же источники. Если была развёрнута предыдущая версия этого репозитория, после приёмки удалить её отдельные adapter Deployment/Service/ServiceMonitor и неиспользуемый config, сверив реальные имена из старого Git commit. Не удалять общие Secrets/CA/Prometheus/Grafana. Старую версию сохранить в Git для rollback.

## Проверка файлов без кластера

```bash
.venv/bin/python scripts/generate_monitoring.py monitoring-config.example.yml
npm install --prefix tests
node tests/check-infinity.cjs
# В среде DevOps, где установлен promtool:
promtool check rules cicd-health/prometheus-recording-rules.yml
```

JSONata fixture tests проверяют transformation logic, но не заменяют smoke test реальных API и установленной версии Infinity. Kubernetes server dry-run, Grafana provisioning, права и доставку уведомлений необходимо проверить в вашей среде.
