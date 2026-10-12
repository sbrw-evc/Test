# CI/CD Operational Health

Актуальная версия переработана по экспорту Grafana v2 и DevOps-ревью от 9 октября 2026. Infinity получает бизнес-данные TeamCity/Jenkins/Octopus из API; Prometheus — состояния pod, ресурсы контейнеров и PVC.

- [Dashboard Grafana v2](cicd-health/cicd-health-dashboard.json)
- [Что изменено и что проверить](docs/REVIEW-RU.md)
- [Пошаговая инструкция DevOps](docs/SETUP-RU.md)
- [Автоматические фильтры пайплайнов](docs/PIPELINE-DISCOVERY-RU.md)
- [Контракт данных](cicd-health/METRICS-CONTRACT.md)
- [Alert provisioning example](cicd-health/grafana-alert-rules.yml)

42 панели, пять главных блоков: Статус, Сейчас, Деплои, Проблемы, Details. В Details четыре свёрнутых ряда: TeamCity, Jenkins, Octopus и Kubernetes/PVC. Kubernetes ограничен `cicd-mia-899` и `octopus-worker-mia-899` даже при выборе All.

Сохранены UID dashboard `cicd-operational-health`, datasource UID, API URLs и текущие автоматические фильтры из переданного экспорта. В `review-source-profile.json` только query templates и настройки, без credentials. При переносе в другую Grafana изменить UID/URLs в profile либо назначить существующие datasource через UI. Сам файл credentials не содержит и в кластере ничего не разворачивает.

Сборка: `python scripts/build_operational_review.py`. Полная подготовка dashboard + фиксированных alert examples: `python scripts/generate_monitoring.py monitoring-config.yml` (PyYAML). Проверки: `python -m unittest discover -s tests -p 'test_*.py'` (нужен jq) и `python scripts/validate_dashboard.py`.

Новый список пайплайнов получается непосредственно QueryVariables из API. Прежние discovery workflow и ручная межсистемная карта больше не требуются. Алерты имеют собственные фиксированные scopes; Octopus rules в примере поставлены на pause до задания реального Prod environment ID.

## Другие проекты репозитория

| Каталог | Назначение |
|---|---|
| `akvorado/` | Скрипт обогащения DstAddr в Akvorado DNS-именами и IP сервисов ([инструкция](akvorado/README.md)) |
