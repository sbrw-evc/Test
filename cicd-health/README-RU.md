# CI/CD Health в Grafana

[Пошаговая настройка DevOps](../docs/SETUP-RU.md) · [Документ Word](../docs/devops-cicd-monitoring-request.docx) · [REST adapter](../adapter) · [Kubernetes manifests](../kubernetes)

Dashboard JSON — 22 панели: статус трёх систем и всей цепочки, доля успешных **последних результатов**, online agents TC/Jenkins, очереди, HTTP response time, последние результаты/длительность и текущие операции. Карточки открывают UI системы и filtered details. Scope задан config.yml; project filter влияет только на operation panels, не на общую очередь/агентов.

Импортировать cicd-health-dashboard.json в Grafana, выбрать Prometheus datasource. Для Grafana-managed alerts datasource UID фиксирован `cicd-prometheus`. Заменить hidden URL constants. Recording rules обязательны: Kubernetes обёртка в ../kubernetes/recording-rules.yml, standalone groups в prometheus-recording-rules.yml. Не применять оба способа одновременно. Grafana alert file содержит 11 правил и удаление двух устаревших UID прежнего комплекта; apply через provisioning/alerting и штатный reload/rollout.

DOWN — наблюдаемая ошибка HTTP/API. UNKNOWN — отсутствующий/неполный/старый сбор. DEGRADED — последний результат неуспешен, очередь выше лимита/ждёт >5min, expected agent offline или online count ниже минимума. HEALTHY — эти проверки в норме. Нельзя считать это доказательством успешной доставки каждого релиза. KPI с устаревшими данными скрыты.

Версия 1 адаптера отдаёт только Gauges, описанные в METRICS-CONTRACT.md. Исключены p95, event counters, success rate за период, свободные совместимые слоты и Octopus worker/target metrics. Failure alert может пропустить краткую ошибку между poll; для каждого события нужен durable webhook/event collector.

Standalone scrape example рассчитан на обычный Prometheus. В Operator кластере использовать ../kubernetes/*.yml. Native names и permissions сверить с установленными версиями. Проверка синтаксиса/mock API не заменяет реальные smoke tests.

Генерация JSON/rules: `python scripts/generate_monitoring.py` из корня, зависимости PyYAML. Примеры community dashboard подходов: [Jenkins Performance and Health](https://grafana.com/grafana/dashboards/9964-jenkins-performance-and-health-overview/), [Octopus Deploy reporting](https://grafana.com/orgs/bhozar/dashboards). Они служат ориентирами по структуре; новый JSON использует наш контракт.
