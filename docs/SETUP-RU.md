# Настройка мониторинга CI/CD по шагам

Просим DevOps подключить TeamCity, Jenkins и Octopus из Kubernetes к Prometheus и Grafana по этой инструкции. Начать с одного build pipeline в каждой CI системе и одного Octopus project/environment. После проверки расширить список. Конфигурация рассчитана на существующий Prometheus Operator / kube-prometheus-stack; без Operator использовать scrape пример из `cicd-health`.

## 1. Заполнить параметры

Работать из корня репозитория. Скопировать `adapter/config.example.yml` в `adapter/config.yml` и заполнить:

| Параметр | Что указать |
|---|---|
| Namespace мониторинга и приложений | В примерах `monitoring` и `cicd`; заменить на фактические |
| URL TeamCity, Jenkins, Octopus | Адрес, доступный из Pod адаптера; сохранить context path Jenkins |
| Prometheus selector label | В YAML `release: kube-prometheus-stack`; сверить с существующим Prometheus CR |
| TeamCity build_type | ID конфигурации сборки, например `Platform_BackendBuild` |
| Jenkins job_path | Относительный URL `job/folder/job/name/job/branch`; URL-encode каждый сегмент |
| Octopus space / project_id / environment_id | Реальные IDs `Spaces-*`, `Projects-*`, `Environments-*` |
| Очередь и timeout | `queue_limit`, `timeout_seconds`; стартовые значения согласовать с командой |
| Агенты | `agents_minimum`; `expected_agents` только для постоянных агентов, не ephemeral Pods |
| Registry | Доступный кластеру registry и immutable tag/digest образа |

`project` и `pipeline` — стабильные подписи в Grafana. Не включать run ID, commit hash и URL в labels. `env` означает контур сбора, а environment Octopus входит в идентификатор pipeline. Не добавлять один scope дважды. После изменения config перезапускать Deployment.

Проверить CRD и selectors:

```bash
kubectl get crd servicemonitors.monitoring.coreos.com probes.monitoring.coreos.com prometheusrules.monitoring.coreos.com
kubectl -n monitoring get prometheus -o yaml
```

В Prometheus CR проверить `serviceMonitorSelector`, `serviceMonitorNamespaceSelector`, `probeSelector`, `probeNamespaceSelector`, `ruleSelector`, `ruleNamespaceSelector`. Они должны выбирать объекты из этой инструкции. При отсутствии CRD сначала установить поддерживаемый в компании monitoring stack; повторно существующий stack не устанавливать.

## 2. Выдать доступ только на чтение

- TeamCity: API token сервисного пользователя с чтением выбранных проектов, очереди и агентов. Для `/app/metrics` требуется **View usage statistics**.
- Jenkins: сервисный пользователь + API token, Overall Read, Job Read для выбранных jobs и доступ на чтение computer/queue. Установить **Prometheus Plugin**, проверить `/prometheus/`. Согласовать права plugin endpoint с политикой установленной версии.
- Octopus: API key с чтением выбранного Space, проектов, environments и deployment tasks. Проверить API по Swagger своей версии Octopus. Запись и запуск деплоев адаптеру не нужны.

Сохранить секреты в корпоративном Secret manager / External Secrets или создать Kubernetes Secret. Ниже вариант для ручного первичного запуска; файл должен содержать `KEY=value`, без кавычек и без завершающих пробелов в значениях:

```bash
install -m 600 /dev/null credentials.env
# Заполнить в редакторе четыре строки:
# TEAMCITY_TOKEN=...
# JENKINS_USER=...
# JENKINS_TOKEN=...
# OCTOPUS_API_KEY=...
kubectl -n monitoring create secret generic cicd-api-credentials --from-env-file=credentials.env --dry-run=client -o yaml | kubectl apply -f -
kubectl -n monitoring create secret generic cicd-native-credentials --from-env-file=credentials.env --dry-run=client -o yaml | kubectl apply -f -
```

После переноса в Secret manager удалить локальный файл. Не добавлять секреты в Git. Native Secret можно ограничить первыми тремя ключами. Secret для ServiceMonitor должен находиться в namespace самого ServiceMonitor.

## 3. Проверить сеть и сертификаты

Разрешить из monitoring: адаптер → REST API трёх систем, Prometheus → adapter:8000 и native endpoints, blackbox → HTTP URLs трёх систем, DNS TCP/UDP 53. Уточнить действующие NetworkPolicy и доступ через service mesh. Новую изоляцию source Pods без проверки остальных потоков не включать.

Использовать TLS verification. Если CA внутренний, создать ConfigMap `cicd-ca` с `ca.crt`, указать `ca_file: /ca/ca.crt` в каждом соответствующем source config и применить `kubernetes/adapter-ca.patch.yml` после установки адаптера. В native ServiceMonitor поправить `tlsConfig.serverName` под сертификат Service; CA должна быть доступна в namespace ServiceMonitor. Для существующего blackbox exporter также смонтировать CA и указать путь в модуле. `insecure_skip_verify` не включать.

```bash
kubectl -n monitoring create configmap cicd-ca --from-file=ca.crt=/secure/path/company-ca.crt --dry-run=client -o yaml | kubectl apply -f -
```

Примеры native YAML используют HTTPS Service port `https`. Для фактического HTTP внутри защищённого кластера указать `scheme: http`, правильное имя порта и удалить `tlsConfig`. URL адаптера может идти через Ingress, но named port ServiceMonitor относится к Service, а не Ingress. Добавить `imagePullSecrets`, если registry закрытый.

## 4. Собрать и запустить адаптер

Это код из `adapter/exporter.py`, а не ссылка на ещё не разработанный exporter. Он выполняет только GET и отдаёт снимок метрик на `/metrics`. Дополнительные агенты внутри TeamCity/Jenkins/Octopus устанавливать не нужно.

```bash
docker build -t REGISTRY/monitoring/cicd-adapter:1.0.0 adapter
docker push REGISTRY/monitoring/cicd-adapter:1.0.0
kubectl -n monitoring create configmap cicd-adapter-config --from-file=config.yml=adapter/config.yml --dry-run=client -o yaml | kubectl apply -f -
# Заменить image и release label в kubernetes/adapter.yml
kubectl apply --dry-run=server -f kubernetes/adapter.yml
kubectl apply -f kubernetes/adapter.yml
# Только для внутренней CA:
kubectl -n monitoring patch deployment cicd-adapter --patch-file kubernetes/adapter-ca.patch.yml
kubectl -n monitoring rollout status deployment/cicd-adapter
kubectl -n monitoring logs deployment/cicd-adapter
kubectl -n monitoring port-forward service/cicd-adapter 8000:8000
```

В другом терминале: `curl -fsS http://localhost:8000/metrics`. Ожидаются `ci_api_up=1` и `ci_collection_complete=1` для всех трёх компонентов; полный timestamp должен обновляться. `/healthz` и `/readyz` проверяют процесс: при недоступности источника Pod продолжает отдавать метрики с ошибкой, чтобы Prometheus видел проблему. PVC не нужен: экспортируются снимки, а история хранится в Prometheus. Реплик одна; увеличение числа реплик без изменения агрегирования удвоит KPI.

При изменении ConfigMap или Secret: `kubectl -n monitoring rollout restart deployment/cicd-adapter`.

## 5. Подключить источники к Prometheus

1. В существующих Service TeamCity/Jenkins добавить labels `monitoring-source: teamcity|jenkins`, `component: teamcity|jenkins`, `env: prod`. Не менять Service selectors. В `kubernetes/native-metrics.example.yml` поправить namespace, named port, context path, CA и release label.
2. Применить native ServiceMonitor. Endpoint TeamCity `/app/metrics`, Jenkins `/prometheus/`.
3. Подключить существующий OSS **prometheus/blackbox_exporter** (если нет — установить через корпоративный Helm/GitOps процесс). Включить `http_2xx` из `cicd-health/blackbox.example.yml`. Адрес его Service должен совпадать с `prober.url` в `kubernetes/probes.example.yml`.
4. В Probe заменить URLs. TeamCity проверять `/healthCheck/ready`; для Jenkins `/login` и Octopus `/` это HTTP reachability, а доступ к данным проверяет адаптер. Создать **один основной Probe на компонент**; дополнительные внешние проверки использовать с другим jobName.
5. Применить Probe и recording rules, предварительно заменив env/release label. Если env отличается от prod, также исправить inventory alert в `grafana-alert-rules.yml`.

```bash
kubectl apply --dry-run=server -f kubernetes/native-metrics.example.yml -f kubernetes/probes.example.yml -f kubernetes/recording-rules.yml
kubectl apply -f kubernetes/native-metrics.example.yml -f kubernetes/probes.example.yml -f kubernetes/recording-rules.yml
```

В Prometheus Targets должны быть UP: `cicd-adapter`, `teamcity`, `jenkins`, три `cicd-http` targets. UP blackbox target означает удачный scrape exporter; дополнительно проверить `probe_success=1`. В Graph проверить `ci_api_up`, `ci_collection_complete`, `ci:operational_state`. Примерное время появления полного статуса: два-три цикла опроса/evaluation.

Если target отсутствует — проверить selectors, namespaces, Service labels и named ports. Если DOWN — сеть, TLS, credentials и HTTP path. Если adapter complete=0 при api=1 — проверить схему API и лимит страниц; не повышать лимит без оценки нагрузки.

## 6. Подключить Grafana и уведомления

1. Создать Prometheus datasource с UID **cicd-prometheus**, URL существующего query endpoint. Шаблон `cicd-health/grafana-datasource.example.yml`. Доступ для querying и alert evaluation должен работать из Grafana server.
2. Import `cicd-health/cicd-health-dashboard.json`, выбрать datasource. В hidden constants `teamcity_url`, `jenkins_url`, `octopus_url` заменить ссылки. Карточки открывают UI системы и детали с component фильтром.
3. Через GitOps/Helm смонтировать `cicd-health/grafana-alert-rules.yml` в `/etc/grafana/provisioning/alerting/` и выполнить штатный rollout/reload Grafana. Это **Grafana-managed alerts**, не Kubernetes PrometheusRule с alert expressions. При обновлении прежнего комплекта секция `deleteRules` удаляет устаревшие правила `cicd-capacity` и `cicd-native-scrape`. Остальные старые UID сохраняются с обновлёнными запросами; новое правило `cicd-agents-low` проверяет минимальное число online agents.
4. В Alerting → Contact points создать Teams contact point через разрешённый tenant **Teams Workflow webhook** и PagerDuty через integration key. Значения хранить в Secret, не Git. Нажать Test для обоих.
5. Notification policies: `domain=cicd,severity=critical` → PagerDuty и Teams; `domain=cicd,severity=warning` → Teams. Если используются sibling routes, настроить continue, чтобы critical дошёл до обоих каналов. Группировать по env/component/alertname; начальные group_wait 30 s, group_interval 5 min, repeat_interval 4 h. Обеспечить egress Grafana к обоим сервисам.
6. Настроить отдельное уведомление о Grafana datasource errors (`DatasourceError`), чтобы ошибка мониторинга не терялась. Пороги 2 s / 15 min / agents_minimum согласовать и проверить в тестовом контуре.

## 7. Принять результат

Проверки делать в согласованном тестовом контуре, не останавливать production:

- Все три карточки отображают реальный статус; при остановке адаптера становятся UNKNOWN, а не HEALTHY. KPI старше 180 s скрываются.
- Запустить по одной успешной и неуспешной сборке и деплою. Удержать неуспешный результат хотя бы два цикла опроса. Проверить таблицу, длительность и failure notification.
- Через тестовый credentials/network scenario подтвердить DOWN при неуспешном API/HTTP check; после восстановления — актуальные значения.
- Временно снизить лимиты очереди/агентов в тестовой конфигурации и проверить соответствующие алерты. Для agent offline добавить постоянный тестовый ID в expected_agents.
- Убедиться, что неизвестные pipeline и чужие Octopus environments/runbooks не попадают в очередь и результаты. Jobs без запусков показывают NO DATA, а не успех.
- Проверить Test и реальную firing/resolved доставку в Teams/PagerDuty, ссылки, владельца и runbook.

Передать команде: URL dashboard, перечень scopes, выбранные пороги, версии систем/плагинов, commit образа, результат проверок и ответственного. Затем сделать Grafana основным мониторингом; прототип отключить после согласованного параллельного наблюдения, либо оставить на большом экране.

## Что включено и что отложено

| Сейчас работает | Пока исключено |
|---|---|
| HTTP/API health и свежесть данных | Свободные совместимые слоты, Octopus worker capacity |
| Последний результат и его длительность по scope | p95 и процент всех запусков за период |
| Доля успешных последних результатов | Счётчики всех событий и алерт на каждую короткую ошибку |
| Очередь и возраст ожидания в настроенных scopes | Состояние всех deployment targets и Octopus agents |
| Число TC/Jenkins online agents, expected stable agent alert | Нехватка агентов с учётом совместимости конкретного job |

Это snapshot MVP. Failure alert остаётся активным до следующего terminal result; один неудачный запуск между двумя опросами может быть пропущен. Для каждого события подключить webhook/event collector с durable storage, дедупликацией и backfill. Не выдавать snapshot percentage за success rate за 24 часа. Jenkins active scan читает все retained builds страницами; для больших history оценить нагрузку, max_pages и retention. Octopus результат выбирается из newest-first terminal tasks по порядку создания, не максимальному CompletedTime; при concurrent deployments это может отличаться от последнего завершившегося. Tenant-specific и runbook KPI пока не включены.

## Проверенные готовые компоненты

| Компонент | Решение и ограничения |
|---|---|
| TeamCity native | `/app/metrics`, встроенный endpoint; не заменяет per-pipeline API snapshots |
| TeamCity OSS exporter | [octopusden/octopus-teamcity-prometheus-exporter](https://github.com/octopusden/octopus-teamcity-prometheus-exporter), Apache-2.0; статусы по templates, без наших queue/agent scopes. Альтернатива для TC last status, одновременно с адаптером одинаковые метрики не собирать |
| Jenkins | [jenkinsci/prometheus-plugin](https://github.com/jenkinsci/prometheus-plugin), Apache-2.0; использовать native metrics, унификацию последних результатов делает адаптер |
| HTTP checks | [prometheus/blackbox_exporter](https://github.com/prometheus/blackbox_exporter), Apache-2.0 |
| Octopus Deploy | Готового exporter с нужным подтверждённым контрактом в рассмотренных источниках не найдено; используется наш REST adapter. `octopus-agile-exporter` относится к Octopus Energy и не подходит |

Источники для сверки версии: [TeamCity metrics](https://www.jetbrains.com/help/teamcity/teamcity-monitoring-and-diagnostics.html), [TeamCity REST](https://www.jetbrains.com/help/teamcity/rest/get-build-details.html), [Jenkins API](https://www.jenkins.io/doc/book/using/remote-access-api/), [Octopus tasks](https://octopus.com/docs/api/tasks), [Prometheus Operator API](https://prometheus-operator.dev/docs/api-reference/api/). Проверено 05 октября 2026; реальные endpoints/права подтвердить на установленной версии до production rollout.
