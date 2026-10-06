# Grafana Native и REST

Установка: [docs/SETUP-RU.md](../docs/SETUP-RU.md). Исследованные OSS решения: [docs/OSS-SOURCES.md](../docs/OSS-SOURCES.md).

Импортировать cicd-health-dashboard.json; provision datasources с UID cicd-prometheus, teamcity-api, octopus-api. Обязателен установленный yesoreyeram-infinity-datasource с backend JSONata поддержкой. В JSON `parser: backend` означает JSONata. Grafana-managed alert rules берут fixed URLs напрямую; frontend parsers и dashboard variables не участвуют в alerts.

Перед генерацией скопировать ../monitoring-config.example.yml в ../monitoring-config.yml и заполнить env,URLs,IDs,job/node regex, один Jenkins label pool, queue/min-agent/timeouts. Из корня: `python scripts/generate_monitoring.py monitoring-config.yml` (PyYAML). Generator не является exporter: его запускают только для подготовки конфигураций, в кластере собственного кода нет.

26 panels: three component health + overall selected scope, latest results, online agents, idle executors, queue, duration/latency trends, active operations и details. TeamCity/Octopus REST не получает временной истории от Prometheus. Все scopes фиксированы конфигурацией; для другого buildType/environment нужно сгенерировать отдельный комплект с отдельными dashboard/alert UIDs либо расширить query templates и alerts при подготовке rollout. Не оставлять неподключённые проекты под общим HEALTHY обещанием.

Recording rules применить ровно одним способом: standalone groups или Kubernetes PrometheusRule. Новый Grafana alert file удаляет старые cicd-* rules и создаёт oss-* rules. Сначала проверить provisioning в staging; после настройки удалить старые adapter Deployment/Service/ServiceMonitor/ConfigMap/Secret. Точная миграция в SETUP-RU.md.

Проверки: `tests/check-infinity.cjs` с JSONata2.1 и offline PromQL parsing. Требуется реальный smoke test вашей Grafana/Infinity/API/plugin версии; тесты query expressions не доказывают совместимость всех deployed версий. Без access credentials к кластеру комплект не считается установленным.
