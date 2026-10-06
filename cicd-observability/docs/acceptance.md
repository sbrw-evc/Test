# Приёмка в вашем Kubernetes

Локально проверены JSON/YAML, геометрия панелей и JQ на fixtures. Живая Grafana,
реальные API и Teams/PagerDuty не были доступны во время подготовки.

1. Datasource Save & Test + Query Inspector: JSON API, не HTML login/SSO; correct Space.
   Базовый health test Infinity не заменяет выполнение реального запроса.
2. Prometheus Targets: 2 native scrapes UP и 3 Blackbox probes с component labels.
   TeamCity `/healthCheck/ready`; проверить, что отключение backend даёт probe_success=0.
3. Убедиться, что kube-state-metrics/cAdvisor series присутствуют в нужных namespaces.
4. Импортировать 4 JSON или подключить provider; проверить health expressions и ссылки,
   что карточки H видны, а промежуточные результаты не показываются в Stat cards.
5. Сверить последние N результатов, очереди и agents с интерфейсами CI/CD. Проверить
   nested Jenkins folders, aborted/unstable, canceled TC, Failed/TimedOut Octopus.
6. Сопоставить внешние Octopus workers с worker pools, built-in workers и targets;
   при dedicated pools задать min_agents.octopus>0 и перегенерировать rules.
7. На тестовых заданиях получить failure после success: failure alert должен сработать
   и восстановиться через 10 минут после последнего failure. Проверить описание
   агрегированного уведомления, а не ожидать уведомление на каждый результат.
8. Отключить постоянный агент вне maintenance: через 5 минут offline alert. Пометить
   maintenance/temporarilyOffline/disabled: такой агент не должен считаться unexpected.
   Autoscaling ephemeral agents требуют отдельных scopes или silence policy.
9. Проверить queue/capacity/long-running на тестовом workload с безопасными временными
   порогами. Учитывать labels/pools compatibility: глобальная capacity не гарантирует
   наличие подходящего executor для отдельного job.
10. Проверить revoked token, 403, timeout, неполный инвентарь >limit: Нет данных / alert,
    не зелёный. Сравнить точное поведение NoData и Error в используемой Grafana версии.
11. Проверить Test contact point и реальное firing/resolved сообщение в Teams и PagerDuty;
    сохранить существующие unrelated routes. Настроить maintenance windows.
12. Измерить нагрузку Infinity запросов на Jenkins tree и остальные API при 1 минуте,
    проверить lag и limits. Проверить версии/имена native metrics.
13. Отключить один server pod на тестовом окружении: проверить Ready/HTTP alerts, затем
    восстановление. Не тестировать остановкой production без согласованного окна.
14. Проверить внешний ingress отдельно, если его доступность входит в SLA: внутренний
    Service probe не обнаружит сбой ingress/DNS снаружи кластера.
15. Провести параллельный запуск, назначить владельцев, записать согласованные пороги,
    проверить kiosk на целевом экране. Только после приёмки вывести прототип из работы.

Rollback: убрать новые ConfigMap mounts/provider/rule group и дополнительные scrape
jobs через ваш GitOps/Helm процесс; не удалять существующие sources/routes. Dashboard
UIDs и alert rule UIDs имеют префикс `cicd-`; новые policies не подключаются по умолчанию.
