# Автоматическое заполнение фильтров

Текущий рабочий dashboard использует Infinity QueryVariables непосредственно из API:

- TeamCity buildTypes — текст project/name, значение configuration ID;
- Jenkins jobs — recursive folders, значение encoded REST job path;
- Octopus Space/projects — names/IDs, зависимые options;
- Octopus Prod environment — только точные имена Prod/Production;
- Kubernetes pod options — только два заданных CI namespace.

Не требуется вручную заполнять межсистемную карту `pipelines`, запускать собственный exporter или делать commit списка после каждого добавленного проекта. Списки обновляются при загрузке dashboard; hidden deployment metadata и executor map — при обновлении временного диапазона. После создания проекта/ветки обновить dashboard и проверить, что API account видит объект.

Source query models находятся в `cicd-health/review-source-profile.json`. При смене endpoints, datasource UID, folder depth или inventory limit изменить profile и пересобрать JSON через `scripts/build_operational_review.py`. API ошибки и truncated inventory не заменяются неполным подтверждённым списком.

Предыдущий `discover_pipelines.py` и GitHub cron workflow удалены: их статическая карта больше не управляет этим dashboard. Автоматический выбор одного логического pipeline сразу во всех трёх системах нельзя вывести из одинаковых имён без явного соглашения; в рабочем экспорте сохранены отдельные фильтры TeamCity/Jenkins/Octopus.

Для server-side alerts по-прежнему нужен фиксированный scope в `monitoring-config.yml`: Grafana alert rules не читают текущие dropdown values.
