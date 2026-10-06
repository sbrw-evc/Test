# CI/CD Health без собственного exporter

TeamCity native `/app/metrics` + Jenkins Prometheus Plugin + Prometheus Blackbox Exporter + Grafana Infinity REST для Octopus и TeamCity per-build KPI.

- [Пошаговая инструкция DevOps](docs/SETUP-RU.md)
- [Документ Word](docs/devops-cicd-monitoring-request.docx)
- [Выбор open-source компонентов и аналогичные dashboards](docs/OSS-SOURCES.md)
- [Dashboard и alert rules](cicd-health)
- [Kubernetes ServiceMonitor / Probe / recording rules](kubernetes)
- [Контракт данных и ограничения](cicd-health/METRICS-CONTRACT.md)

26 панелей, 18 Grafana-managed alerts, 13 live REST query templates. Свой адаптер удалён. Для своего кластера заполнить monitoring-config.yml и сгенерировать фиксированные URLs/scopes. Реальные секреты и конфиг не коммитить.

В кластере ничего не развёрнуто. История REST не хранится в Prometheus; последние результаты не являются процентом за период. Polling не гарантирует уведомление о каждом быстром fail→success. Неподдерживаемые capacity/p95 KPI исключены.

## Структура комплекта

| Каталог | Назначение |
|---|---|
| `docs/` | Пошаговая инструкция, Word-документ и выбор OSS-компонентов |
| `cicd-health/` | Dashboard, datasources, alerts, REST queries и конфигурация blackbox |
| `kubernetes/` | ServiceMonitor, Probe и PrometheusRule для кластера |
| `scripts/` | Генерация конфигураций из `monitoring-config.yml` |
| `tests/` | Проверка JSONata-преобразований |

`cicd-health/prometheus-recording-rules.yml` и `kubernetes/recording-rules.yml` генерируются из одной модели: первый нужен для проверки через `promtool`, второй — для применения через Prometheus Operator. В кластере применять только Kubernetes PrometheusRule.

Архив при необходимости можно скачать средствами GitHub через Code → Download ZIP.
