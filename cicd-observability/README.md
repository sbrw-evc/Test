# CI/CD Operational Health — Grafana

Единый operational dashboard для TeamCity, Jenkins и Octopus в Kubernetes.
**Без собственных экспортеров и сервисов:** REST API опрашивает Grafana Infinity;
временные ряды собирает существующий Prometheus. Для HTTP используется официальный
[Prometheus Blackbox Exporter](https://github.com/prometheus/blackbox_exporter).

## Что входит

| Файл | Назначение |
|---|---|
| `dashboards/cicd-health.json` | Общий health, KPI, алерты, HTTP и Kubernetes |
| `dashboards/teamcity-details.json` | Сборки, агенты, длительности и `/app/metrics` |
| `dashboards/jenkins-details.json` | Сборки, ноды, executor capacity и Prometheus Plugin |
| `dashboards/octopus-details.json` | Deploy tasks, workers и отдельный список targets |
| `provisioning/datasources/cicd.yaml` | Prometheus + 3 отдельных Infinity datasource |
| `provisioning/alerting/rules.yaml` | 26 Grafana-managed alert rules, период 1 минута |
| `provisioning/alerting/notifications.yaml.example` | Teams Workflow + PagerDuty, шаблон маршрутизации |
| `kubernetes/prometheus-scrape.yaml` | TeamCity `/app/metrics`, Jenkins `/prometheus/`, HTTP probes |
| `kubernetes/blackbox.yaml` | Только официальный Blackbox Exporter |
| `kubernetes/grafana-values.yaml.example` | Подключение файлов в существующий Grafana Helm release |
| `scripts/generate.py` | Генерация статических JSON/YAML на рабочей машине, не компонент Kubernetes |

Визуальные референсы из публичной галереи Grafana указаны в
[docs/design-references.md](docs/design-references.md). Используются native Stat,
Gauge, State timeline, Timeseries, Table и Alert list, без HTML-плагинов.

## Требования

- Существующий Grafana **11.6+**, Grafana Alerting enabled. Для пакета выбран Infinity
  **4.1.1**; перед внедрением используйте утверждённую вашей платформой совместимую версию.
- Prometheus, kube-state-metrics и kubelet/cAdvisor уже собирают Kubernetes-метрики.
- Jenkins [Prometheus Plugin](https://plugins.jenkins.io/prometheus/) установлен и настроен.
- Read-only служебные учётные записи: TeamCity REST и `View usage statistics` для
  `/app/metrics`; Jenkins Overall/Read, Job/Read и просмотр агентов;
  Octopus доступ к выбранному Space, задачам, workers и targets.
- Grafana имеет сетевой доступ к API; Prometheus — к metric endpoints и Blackbox;
  Blackbox — к серверам. Сертификаты должны проходить проверку.
- Согласованный namespace `monitoring`; адреса и порты в примерах замените своими.

Это подготовленный пакет для внедрения, не уже подключённая production-инсталляция.
Без адресов, версии API, токенов и доступа к кластеру живые запросы и доставка
уведомлений не проверялись. Проверьте их по [acceptance checklist](docs/acceptance.md).

## Настройка и подключение

### 1. Настроить область данных

Работайте из папки `cicd-observability`:

```bash
cp config.example.json config.json
# Отредактировать Space ID, пороги, ссылки, namespaces, Jenkins folder depth.
python -m pip install -r requirements-dev.txt
python scripts/generate.py config.json
python -m unittest discover -s tests -v
```

Для тестов требуется CLI `jq`. `config.json` не коммитится. Генератор обновляет четыре
дашборда, alert rules и query catalog; после изменений коммитьте сгенерированные файлы.
`namespace_regex`/`pod_regex` определяют область Kubernetes-алерта, а textbox-переменные
дашборда меняют только отображение. Выберите server pods: временные build agents могут
намеренно быть Pending/NotReady/удаляться, их не стоит включать в общий server-pod alert.
`public_urls` — ссылки, открываемые пользователем; адреса API ниже могут быть внутренними.

### 2. Секреты и datasource

Создайте реальный env-файл **вне репозитория**, опираясь на
`kubernetes/secret.env.example`. Не помещайте токены в dashboard JSON, URL или ConfigMap.
URL — корень приложения без завершающего `/`; для Jenkins с context path включите его
в `JENKINS_URL`, например `https://ci.example.com/jenkins`.

```bash
kubectl -n monitoring create secret generic cicd-grafana-api \
  --from-env-file=/secure/path/cicd-grafana.env
kubectl -n monitoring create configmap cicd-datasources \
  --from-file=cicd.yaml=provisioning/datasources/cicd.yaml
kubectl -n monitoring create configmap cicd-dashboard-provider \
  --from-file=cicd.yaml=provisioning/dashboards/cicd.yaml
kubectl -n monitoring create configmap cicd-dashboard-json \
  --from-file=dashboards/
kubectl -n monitoring create configmap cicd-alert-rules \
  --from-file=rules.yaml=provisioning/alerting/rules.yaml
```

Внесите mounts и `envFromSecret` из `grafana-values.yaml.example` в текущий Helm release.
Не перезаписывайте весь values-файл. Provisioning выполняется при перезапуске Grafana;
после изменения правил/источников используйте штатный rollout/reload вашей платформы.
У всех datasource фиксированные UIDs, поэтому dashboard и alerts используют одни источники.
В существующей организации при другом `orgId` измените `1` в provisioning-файлах.

Grafana provisioning разворачивает `$ENV`; в alert rules литералы `$` экранированы как
`$$`, чтобы JQ-переменные и Grafana expressions дошли до движка без изменения.
Не запускайте `envsubst` поверх этих файлов. В JSON dashboards экранирование не нужно.
Если импортируете rules через HTTP API вместо file provisioning, сначала разверните
file-provisioning escaping в литеральные `$` и преобразуйте формат к API schema.

Ручной импорт: сначала настройте datasource с этими UIDs, затем импортируйте 4 JSON
через Dashboards → New → Import. Это импортирует dashboard, **не alert rules**.

### 3. Prometheus и HTTP

Включите `/app/metrics` и `/prometheus/`, проверьте их от имени служебной учётки.
Настройте авторизацию scrapes через существующий механизм вашей платформы:
TeamCity Bearer token, Jenkins basic auth/API token. Credentials должны быть доступны
Prometheus, отдельно от Grafana. Публичный scrape-шаблон не содержит путей к Secrets
или внутренней DNS-топологии: только явно вымышленные `.example.invalid` endpoints.
Для TLS endpoints настройте доверенный CA, если он частный.

Добавьте список из `prometheus-scrape.yaml` в существующий `scrape_configs`, либо в
`prometheus.prometheusSpec.additionalScrapeConfigs` kube-prometheus-stack. Это явно вымышленные
адреса `.example.invalid`, не auto-discovery ваших реальных ресурсов. Не меняйте job labels
`cicd-teamcity`, `cicd-jenkins`, `cicd-blackbox` без изменения dashboard/alerts.

Если у вас ещё нет утверждённого Blackbox Exporter, согласуйте официальный образ и
его digest, затем используйте `blackbox.yaml`; иначе переиспользуйте существующий
Blackbox и поменяйте replacement address в relabeling. Никакие custom images не нужны.
TeamCity probe направьте на `/healthCheck/ready`; Jenkins и Octopus — на проверенные
200 endpoints. Probe логина измеряет доступность HTTP, не полную готовность бизнес-API;
API проверяется отдельным alert rule. Редирект на SSO намеренно не принимается за 200.

### 4. Уведомления

Создайте Teams **Workflow** «Post to a channel when a webhook request is received» и
PagerDuty Events API integration key. Настройте contact points и test delivery.
Шаблон `notifications.yaml.example` по умолчанию **не подключён**, чтобы не затереть
существующее дерево уведомлений и не пытаться отправлять на placeholder URLs.
Добавьте в существующую notification policy маршрут `team=cicd` → Teams и маршрут
`team=cicd,severity=critical` → PagerDuty, сохранив остальные маршруты. Для новой
изолированной Grafana можно смонтировать заполненный шаблон целиком.
Все правила используют `team=cicd`; failures/server/API errors — critical.

## Как интерпретировать показатели

| Показатель | Определение |
|---|---|
| Общий health | Худшее состояние из TeamCity/Jenkins/Octopus; ошибка API → Нет данных |
| ЗДОРОВ | HTTP=200; агенты не ниже минимума; нет неожиданно offline; очередь/текущая длительность ниже порога; нет failure за последние 10 минут |
| ДЕГРАДАЦИЯ | HTTP доступен, один из operational KPI нарушен |
| НЕДОСТУПЕН | Blackbox probe=0 или ожидаемый probe отсутствует |
| Успешность | SUCCESS / (SUCCESS + FAILURE) в последних N завершённых результатах |
| Jenkins UNSTABLE | Считается failure; ABORTED/NOT_BUILT исключаются из успешности |
| TeamCity | Все branch-ы (`branch:default:any`), завершённые результаты; canceled исключаются |
| Octopus | Только tasks `name=Deploy`; Success/Failed/TimedOut, canceled исключаются из успешности |
| Агенты TeamCity | connected + enabled + authorized; не обязательно idle или совместимы с конкретной сборкой |
| Агенты Jenkins | Online nodes с executors>0, без temporarilyOffline; это nodes, не количество executor slots |
| Octopus workers | Healthy + enabled; deployment targets и built-in worker — не этот показатель |
| Длительность | Среднее по последним N завершённым результатам с датами start/finish, включая canceled |
| Текущее долгое выполнение | Максимальный возраст running build / Executing,Cancelling Deploy task |
| HTTP response time | Blackbox `probe_duration_seconds`: полная probe, включая DNS/TCP/TLS/HTTP |

Для Octopus минимум workers по умолчанию **0**: built-in worker не представлен как
обычный внешний worker. Если нужны dedicated workers, задайте минимум >0 — появится
и отдельный capacity alert. Количество Healthy workers не измеряет свободные slots,
а min_agents не гарантирует совместимость сборки с pool/label. Для production
рекомендуется отдельно согласовать пороги и scopes каждого пула.

KPI Infinity — **текущие snapshots**, они не превращаются автоматически в историю
Prometheus. Time picker управляет HTTP/Kubernetes/native Prometheus graphs;
«последние N» остаются выборкой API. Jenkins выбирает последние N завершённых глобально
из последних N каждого возвращённого job; вложенность folders задаётся конфигурацией.

## Алерты и ограничения опроса

- API evaluation: 1 минута; failure — любое неуспешное завершение в последних 600 сек.
- HTTP down: 2 мин; API error/incomplete inventory: 2 мин; scrape down: 3 мин.
- Offline agents: 5 мин, min capacity: 5 мин; queue >10: 5 мин.
- Running build >1800 сек / deploy >3600 сек: 5 мин; HTTP probe >3 сек: 5 мин.
- Kubernetes pod not Ready: 5 мин. `NoData` и execution error требуют внимания;
  они не трактуются как успешное выполнение и не доказывают failure сборки.
- Failure alert **агрегирован по компоненту**, не отправляет отдельное уведомление на
  каждый build/deploy; в деталях есть workflow, result ID и ссылки.
- Последние N ограничены 100. Если за окно между опросами завершится больше N задач,
  событие может выпасть из выборки. Retention API, permissions, folder depth и нагрузка
  также влияют на полноту. Для гарантированного event-per-build/deploy используйте
  штатные уведомления/вебхуки CI/CD; один REST polling такой гарантии не даёт.
- Agent/queue/running inventories ограничены 1000. Ответ с nextHref/TotalResults,
  указывающими на обрезанный список, вызывает parser error, а не ложное «здоров».
- Infinity делает независимые запросы каждой панели/правила; большой Jenkins tree
  может быть дорогим. Измерьте нагрузку до внедрения, задайте минимально нужный scope,
  N и refresh. Не ускоряйте polling без нагрузочной проверки.
- Нативные TeamCity/Jenkins metric names зависят от версии/настроек. TeamCity extra
  experimental metrics могут требовать `?experimental=true`; Jenkins prefix задаётся
  в конфигурации. Отсутствующая native metric → No data, без подстановки нуля.
- Availability за период — доля успешных полученных probes, не SLA с полной поправкой
  на пробелы scrape. Сами пробелы контролируются сбором/alerting.

## Ввод в эксплуатацию

Пройдите [docs/acceptance.md](docs/acceptance.md), протестируйте route в Teams/PagerDuty,
назначьте владельцев и согласуйте пороги. После параллельной эксплуатации и приёмки
сделайте Grafana основной точкой мониторинга. Затем выключайте прототип и его старые
alerts, либо оставьте только kiosk экран Grafana `/d/cicd-operational-health?kiosk`.
Пакет не удаляет прототип и не изменяет кластер автоматически.

## Источники API

- [TeamCity metrics и readiness](https://www.jetbrains.com/help/teamcity/teamcity-monitoring-and-diagnostics.html)
- [TeamCity Build REST schema](https://www.jetbrains.com/help/teamcity/rest/build.html)
- [TeamCity Agent REST schema](https://www.jetbrains.com/help/teamcity/rest/agent.html)
- [Jenkins Remote Access API](https://www.jenkins.io/doc/book/using/remote-access-api/)
- [Jenkins Prometheus Plugin metrics](https://github.com/jenkinsci/prometheus-plugin/blob/master/docs/metrics/index.md)
- [Octopus Tasks API](https://octopus.com/docs/api/tasks)
- [Infinity backend JQ](https://grafana.com/docs/plugins/yesoreyeram-infinity-datasource/latest/query/jq-backend/)
- [Teams Workflow integration](https://grafana.com/docs/grafana/latest/alerting/configure-notifications/manage-contact-points/integrations/configure-teams/)
