# Мониторинг TeamCity Jenkins и Octopus в Kubernetes

Готовый комплект для первой рабочей версии Grafana CI/CD Health: dashboard, recording rules, Grafana-managed alerts, REST adapter и Kubernetes templates.

Начать с [пошаговой инструкции DevOps](docs/SETUP-RU.md). Для пересылки: [документ Word](docs/devops-cicd-monitoring-request.docx).

- [Dashboard и rules](cicd-health)
- [Код адаптера и тесты](adapter)
- [Манифесты Kubernetes](kubernetes)
- [Точный контракт метрик и ограничения](cicd-health/METRICS-CONTRACT.md)
- [Архив комплекта](cicd-health-grafana-kit.zip)

Первая версия показывает последние результаты, длительность, очереди выбранных pipeline, online agents TeamCity/Jenkins, HTTP/API health и свежесть. Недостоверные пока KPI (p95, процент запусков за период, Octopus free slots) исключены. Все параметры, image registry, IDs и secrets необходимо задать для своего кластера. Файлы являются шаблонами внедрения; в реальном кластере ничего не установлено.
