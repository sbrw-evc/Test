# Изменения по ревью CI CD Operational Health

Основа — переданный рабочий экспорт `dashboard.grafana.app/v2`, а не старый пример из репозитория. Требования из скриншотов ревью учтены в раскладке и запросах. Сохранены реальные datasource UID и API URLs. В реальной Grafana и API изменения ещё нужно проверить; локальные tests не подтверждают права/формат вашей установленной версии.

## Поправить

| Замечание | Реализация |
|---|---|
| DEGRADED без причины | Под каждым статусом таблица причин из тех же A–F запросов и порогов. Ссылки ведут на причины. API error остаётся Error/No data, не healthy |
| Completed results малоинформативны | Failures первыми, цвет всей строки, номер как ссылка, branch/release context и actor. Недостающие поля явно отмечены |
| Не видно зависших executions | Агент, age, обычная длительность успешных запусков того же pipeline, отношение age / usual; минимум 3 успешных samples |
| Pods показывают весь кластер | Во всех Prometheus expressions неизменный namespace scope `cicd-mia-899|octopus-worker-mia-899`. Requests/limits и читаемые легенды компонентов; не threshold 80 CPU cores/restarts |
| Разные названия метрик | TeamCity/Jenkins используют «сборка/агент», Octopus — «деплой/workers»; одинаковые названия результатов/длительности. Описания сокращены |
| Неясен time range | Первый ряд подписан «текущий REST snapshot». Prometheus graphs/annotations используют выбранный range. Prod failures — скользящие 24h от текущего времени, независимо от timepicker |

## Добавить

| Требование | Реализация и границы |
|---|---|
| PVC заполнение | Все PVC двух CI namespace; warning 80%, critical 90%. Линейное время до заполнения по снижению available за 6h; отдельный predict_linear на 7d |
| Nexus | Pod Ready и общий PVC view. Зависимость видна рядом с CI состояниями |
| Octopus по Prod | Автоматический environment dropdown только с точными именами Prod/Production. Реальный ID не выдумывается. Последний видимый завершённый task по каждому проекту + filtered server-side completion interval для failures |
| Проблемные pipeline | Group by workflow; failures, mean/max duration, sample size, success/failure alternations. «Похоже на flaky» — только эвристика, не доказательство тестовой нестабильности |
| Очередь | Количество задач, возраст старейшей и уникальные причины. TeamCity waitReason/queuedDate, Jenkins why/inQueueSince, Octopus queue timestamps/preconditions |
| Загрузка агентов | TC build assigned / enabled online agents; Jenkins idle=false agents / online agents. Busy Octopus workers не выводится из HealthStatus: доступный API не даёт гарантированную worker allocation; на панели явная пометка |
| Server warnings | TC /app/rest/health: global:true и project:(affectedProject:_Root), minSeverity:warn; Jenkins offlineCauseReason; Octopus Tentacle target HealthStatus/StatusSummary |
| Аннотации | Restart events по увеличению counter в 5m windows и Prod deployment completion timestamps. Restart marker — windowed observation, а не точная секунда рестарта |

Для Octopus tasks не гарантированы ProjectId/release/actor. Hidden QueryVariable считывает последние 1000 deployments и связывает metadata по TaskId; отдельный projects lookup добавляет имя к ProjectId. Actor берётся из DeployedBy; LastModifiedBy не считается инициатором. ReleaseVersion берётся при наличии, иначе показывается ReleaseId. Lookup bounded: если TaskId не попал в lookup, поле отмечается как недоступное; неподтверждённые ProjectId исключаются из ranking/матрицы. В зависимости от вашей версии API могут понадобиться project release lookup или увеличение inventory limit для точного Version.

Jenkins Pipeline может не отдавать builtOn. Hidden executor map связывает URL running build с computer/executor. Если executor не виден аккаунту или это завершённая задача, агент может остаться неизвестным. Для Octopus ServerNodeId обозначает сервер обработки, а не назначенного worker. Timestamp Jenkins включает queue wait: age не гарантирует чистое execution time. TeamCity и Octopus age используют start timestamps.

Baseline ограничен последними 100: среднее успешных durations того же workflow, минимум 3 samples. Flaky — ≥4 success/failure outcomes и переходы в ≥50% соседних пар. Это не полная история, не performance SLO и не event log.

## Убрать

Удалены повторяющиеся KPI из Details, отдельные SUCCESS/FAILURE sample count tiles, пустой общий KPI ряд, повтор заголовка dashboard, custom timezone variable/Apply link и дублирующие details links в шапке. Built-in timezone Grafana сохранён. Детали теперь содержат таблицы; ресурсные графики — в свёрнутом Kubernetes/PVC ряду.

## Приёмка в dev

### Исправление ошибок со скриншотов после импорта

- `reducer mode 'strict' is not supported`: в JSON запроса Reduce нельзя указывать `settings.mode: strict`. Строгий режим включается по умолчанию при отсутствии `mode`; явные значения backend — только `dropNN` и `replaceNN`. Убран неверный параметр в 18 Reduce-запросах статусов и 20 правилах алертов, без замены отсутствующих данных нулём. Контракт проверен по [Grafana UnmarshalReduceCommand](https://github.com/grafana/grafana/blob/main/pkg/expr/commands.go).
- `fromjson cannot be applied to: object`: скрытые переменные metadata уже могут подставляться объектами, а не строками JSON. Общий `json_object` выполняет `fromjson` только для строки и проверяет тип результата. Исправлены executor lookup Jenkins, metadata и имена проектов Octopus; некорректное значение остаётся ошибкой. Проверки исполняют запросы с обоими вариантами интерполяции, включая кавычки и обратные слеши.
- Причины состояния объединены трансформацией Merge, чтобы не переключать A–F через селектор результатов таблицы. PVC показаны горизонтально со шкалой 0–100% и точностью 0,1%.

После обновления повторно импортировать актуальный JSON с тем же UID и перезагрузить dashboard, чтобы скрытые QueryVariables обновились. Если правила уже импортированы, обновить их из актуального `grafana-alert-rules.yml`, сохранив настроенные contact points. Скриншоты подтвердили две ошибки прежнего экспорта; следующие проверки всё ещё нужно выполнить в рабочей Grafana.

1. Импортировать JSON именно как Grafana v2 Dashboard, UID `cicd-operational-health`. Если импорт в этой версии требует API resource format, использовать штатный Import/Resource API вашей Grafana.
2. Сверить четыре datasource UID из profile. Не заменять read-only credentials в JSON.
3. Убедиться, что environment options содержат фактический Prod/Production и выбран правильный ID. При отсутствии Prod запросы должны ошибаться, а не показывать healthy пустую выборку.
4. Сопоставить причину каждой DEGRADED плитки с реальными queue/offline/failure/age значениями. Проверить числовые пороги и inventory truncation errors.
5. Проверить links по реальной сборке/deploy, branch/actor metadata, Jenkins executor mapping. Не переданные API поля остаются отмеченными.
6. Сравнить baseline и flaky calculation с вручную выбранными четырьмя чередующимися результатами.
7. All namespace/pod не должен расширять metrics за два CI namespace. Настроить pod name regex labels, если серверные prefixes отличаются. Проверить наличие requests/limits и kubelet_volume_stats_*; без них соответствующие графики/прогноз остаются No data.
8. Сверить PVC проценты с kubelet и выбранный horizon. Forecast требует хотя бы 6h наблюдений и отрицательного slope; при смене capacity/resize прогноз нужно оценить вручную.
9. Проверить аннотации на реальном restart и Prod task; затем проверить warnings API permissions.
10. Только после review включать фиксированные alerts/notifications. Алерты не повторяют текущий dropdown choice автоматически.
