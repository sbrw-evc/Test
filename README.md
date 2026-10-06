# CI/CD Health без собственного exporter

TeamCity native `/app/metrics` + Jenkins Prometheus Plugin + Prometheus Blackbox Exporter + Grafana Infinity REST для Octopus и TeamCity per-build KPI.

- [Пошаговая инструкция DevOps](docs/SETUP-RU.md)
- [Документ Word](docs/devops-cicd-monitoring-request.docx)
- [Выбор open-source компонентов и аналогичные dashboards](docs/OSS-SOURCES.md)
- [Dashboard и alert rules](cicd-health)
- [Kubernetes ServiceMonitor / Probe / recording rules](kubernetes)
- [Контракт данных и ограничения](cicd-health/METRICS-CONTRACT.md)
- [Архив](cicd-health-grafana-kit.zip)

26 панелей, 18 Grafana-managed alerts, 13 live REST query templates. Свой адаптер удалён. Для своего кластера заполнить monitoring-config.yml и сгенерировать фиксированные URLs/scopes. Реальные секреты и конфиг не коммитить.

В кластере ничего не развёрнуто. История REST не хранится в Prometheus; последние результаты не являются процентом за период. Polling не гарантирует уведомление о каждом быстром fail→success. Неподдерживаемые capacity/p95 KPI исключены.
