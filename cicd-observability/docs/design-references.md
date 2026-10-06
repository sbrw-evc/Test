# Публичные визуальные референсы

Использованы как композиционные референсы; чужие JSON и лицензируемые assets не
копировались. Запросы адаптированы к Infinity/Prometheus и требованиям этого проекта.

| Публичный dashboard | Что использовано в этом проекте |
|---|---|
| [22842 — Jenkins: Improved Performance and Health Overview](https://grafana.com/grafana/dashboards/22842-jenkins-improved-performance-and-health-overview/) | Иерархия обзорных Stat cards и Timeseries, раздельные блоки workload/capacity |
| [13751 — Jenkins performance and health overview for prometheus-plugin](https://grafana.com/grafana/dashboards/13751-a-jenkins-performance-and-health-overview-for-jenkinsci-prometheus-plugin/) | Группировка executor capacity, очереди и node status; native plugin graphs на details |
| [11669 — TeamCity](https://grafana.com/grafana/dashboards/11669-teamcity/) | Отдельный блок running builds, queued builds и connected authorized agents |
| [18896 — Blackbox Exporter](https://grafana.com/grafana/dashboards/18896-blackbox-exporter/) | Gauge/Stat доступности и Timeseries для HTTP; в проект добавлена state timeline UP/DOWN |

Просмотрены публичные страницы и доступные screenshots Jenkins 22842 и Blackbox
18896. Для TeamCity доступен перечень метрик на странице; screenshot недоступен.

## Композиция

Общий экран: четыре одинаковые крупные health cards → список активных проблем → три
равных колонны KPI → HTTP timeline/latency/availability → Kubernetes resources.
Карточки компонентов ведут на отдельные подробные dashboards с таблицами результатов
и инвентарём агентов. Дополнительные страницы имеют общую навигацию и ссылку в CI/CD UI.

Красный означает недоступность/failure, оранжевый — деградацию, зелёный — подтверждённое
здоровье. Отсутствие данных не преобразуется в зелёный статус. В таблицах результат
выделен цветом; canceled/aborted серые. Процент успеха показан gauge 0–100 с одинаковыми
порогами для трёх компонентов. Time series не соединяют пробелы данных.

Это native Grafana configuration, не HTML-имитация. Реальный rendering и размер
шрифтов нужно принять в установленной версии Grafana на рабочем экране и kiosk.
