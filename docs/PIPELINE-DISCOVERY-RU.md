# Автоматическое заполнение пайплайнов

Список `pipelines` больше не нужно собирать вручную по каждому ID. `scripts/discover_pipelines.py` получает инвентарь из REST API, связывает компоненты по настроенным правилам и записывает готовый список в локальную конфигурацию. `--generate` пересобирает только dashboard. Это задача подготовки конфигурации, а не exporter: собственного сервиса сбора метрик или HTTP endpoint нет.

Для работы нужны доступ к трём API и существующие read-only credentials. В текущем комплекте есть код и готовый workflow; выполнение в вашей сети ещё требует настройки runner и секретов.

## 1. Задать правила имён один раз

Скопировать `monitoring-config.example.yml` в `monitoring-config.yml`, заполнить фактические адреса и базовые scopes. Список `pipelines` можно удалить — discovery запишет его заново. В секции `discovery` задать:

```yaml
discovery:
  teamcity_locator: 'affectedProject:Platform,defaultFilter:false,count:200'
  teamcity_name_field: project
  teamcity_name_regex: '(?P<pipeline>.+)'
  jenkins_name_regex: 'platform/(?P<pipeline>[^/]+)(?:/.*)?'
  octopus_name_regex: '(?P<pipeline>.+)'
  octopus_spaces: [Spaces-1]
  overrides: {}
```

Это пример соглашения, а не реальные имена вашей организации:

| Система | Обнаруженная сущность | Общий ключ |
|---|---|---|
| TeamCity | Проект Backend, buildType Backend_Build | backend |
| Jenkins | platform/backend/build, platform/backend/test | backend |
| Octopus | Проект Backend в Spaces-1 | backend |

Сопоставление строгое: `fullmatch`, затем trim и casefold. Нечёткий поиск по похожим словам не используется. Именованная группа `pipeline` задаёт общий ключ. Для TeamCity можно выбрать `teamcity_name_field: buildType`, чтобы использовать имя конфигурации сборки вместо проекта. Locator должен ограничивать discovery конфигурациями, которые действительно входят в контролируемые цепочки; helper builds без deploy не нужно автоматически объявлять полными пайплайнами.

Jenkins folders и multibranch обходятся рекурсивно. Все подходящие leaf jobs собираются в точный regex, с экранированием специальных символов. TeamCity и Octopus страницы читаются полностью; ошибки и неполные ответы прерывают обновление. Видимость ограничена правами сервисного аккаунта — API discovery не обнаружит скрытые ему проекты.

Space и environment берутся из верхнего уровня config, если нет явного переопределения. Разрешённые Spaces перечислить в `octopus_spaces`. Discovery проверяет существование environment в Space, но не выводит из этого, что project lifecycle допускает deployment туда: это выбранная область мониторинга, не автоматически доказанная связь lifecycle. REST dashboard покажет NO RUNS/UNKNOWN, если там нет deployments.

## 2. Обработать исключения

Если названия различаются, задать в TeamCity build configuration параметры с реальными IDs:

```text
monitoring.pipeline_id = backend
monitoring.pipeline_name = Backend
monitoring.jenkins_job_regex = platform/backend/.*
monitoring.octopus_project = Projects-1
monitoring.octopus_space = Spaces-1
monitoring.octopus_environment = Environments-1
```

Эти параметры доступны через тот же TeamCity REST API. Они имеют приоритет над правилами имён. Имена параметров можно поменять в `discovery.teamcity_parameters`. Не использовать эти поля для паролей или токенов.

Если менять TeamCity нельзя, исключение задать в одном месте:

```yaml
discovery:
  # Остальные naming rules сохранить.
  overrides:
    Backend_Build:
      pipeline_id: backend
      pipeline_name: Backend
      jenkins_job_regex: 'platform/backend/.*'
      octopus_project: Projects-1
      octopus_space: Spaces-1
      octopus_environment: Environments-1
```

Overrides имеют приоритет над TeamCity parameters. При двух одинаково именованных Octopus projects или разных TC projects с одним ключом автоматическая связь отвергается. В TC project с несколькими buildTypes создаётся отдельная запись на каждый buildType; если нужен только release pipeline, сузить locator. Каждый pipeline по-прежнему содержит один TeamCity buildType и один Octopus project/environment.

## 3. Выполнить discovery

Установить зависимости:

```bash
python3 -m venv .venv
.venv/bin/pip install -r scripts/requirements.txt
```

Передать через secret manager/environment: `TEAMCITY_TOKEN`, `JENKINS_USER`, `JENKINS_TOKEN`, `OCTOPUS_API_KEY`. Не записывать токены в YAML, аргументы команд или Git. Для частного CA указать PEM-файлы через `TEAMCITY_CA_FILE`, `JENKINS_CA_FILE`, `OCTOPUS_CA_FILE`. TLS verify включён, redirects не принимаются; задать конечные API URLs с context path.

Сначала проверить результат без изменения config:

```bash
.venv/bin/python scripts/discover_pipelines.py --config monitoring-config.yml
```

Получится `discovery-report.json`: resolved mappings, unresolved reasons и безопасный каталог имён/IDs. Полный набор build parameters и credentials в отчёт не сохраняются. Файл игнорируется Git, поскольку может содержать внутренние имена систем.

После проверки записать список и обновить dashboard одной командой:

```bash
.venv/bin/python scripts/discover_pipelines.py --config monitoring-config.yml --write --generate
.venv/bin/python scripts/validate_dashboard.py
```

При любой ошибке API, пустом результате или unresolved mappings config остаётся прежним. Исправить naming rules/permissions/overrides и повторить. `--allow-partial` явно допускает только resolved entries и исключает unresolved из нового списка; использовать после оценки отчёта. По умолчанию частичное обновление запрещено.

Новые обнаруженные сущности добавляются при следующем успешном запуске; удалённые исчезают из списка. IDs стабильны: явный `pipeline_id` либо `tc-<buildType ID>`. Повторный запуск при том же инвентаре даёт тот же список. `--generate` использует `--dashboard-only`, поэтому alert rules, recording rules и фиксированный REST query catalog не переписываются.

## 4. Включить автоматическое обновление в GitHub

В репозитории есть `.github/workflows/refresh-pipelines.yml`: запуск вручную и каждый час, в 17-ю минуту UTC. До включения job пропускается через `PIPELINE_DISCOVERY_ENABLED`.

1. Подготовить доверенный self-hosted Linux runner с labels `self-hosted`, `linux`, `cicd-monitoring`. Runner должен видеть private API Kubernetes и иметь Python 3 с venv, git, GitHub CLI `gh`, доступ к GitHub и approved Python package registry. Hosted GitHub runner обычно не видит private cluster URLs.
2. В GitHub Actions Secrets добавить четыре credentials и `CICD_MONITORING_CONFIG_YAML` — весь фактический YAML config с naming rules, без API токенов. Config создаётся временно с правами 0600 и очищается по завершении.
3. При частных CA смонтировать PEM на runner и задать Actions Variables `TEAMCITY_CA_FILE`, `JENKINS_CA_FILE`, `OCTOPUS_CA_FILE` путями к файлам. Если системное trust уже настроено, переменные не нужны.
4. Разрешить Actions создавать PR в настройках репозитория и установить Variable `PIPELINE_DISCOVERY_ENABLED=true`.
5. Выполнить Actions → Refresh CI/CD pipeline catalog → Run workflow. Проверить первый draft PR и связанные IDs перед merge.

Workflow читает API, запускает тесты discovery, проверяет dashboard и предлагает **draft PR только с dashboard JSON**. При отсутствии изменений PR не создаётся. Пока предыдущий pipeline PR открыт, новые предложения пропускаются. Автоматического merge и deploy в Grafana нет; после merge импортировать dashboard либо позволить существующему Grafana GitOps/provisioning процессу обновить его. Workflow не меняет список алертов и не коммитит исходный config/секреты/отчёт.

Если GitHub Actions не используется, ту же команду `--write --generate` запускать по расписанию вашим внутренним CI. Сохранять последнюю успешно проверенную конфигурацию и публиковать только dashboard JSON. Постоянно работающий собственный exporter в Kubernetes не требуется.

## 5. Проверить перед эксплуатацией

- Новый pipeline с корректным naming key появился после refresh и меняет все три системы в dashboard.
- Дублирующийся Octopus project либо пропавшая Jenkins job дают unresolved и сохраняют старую конфигурацию.
- Неверный токен/API error не приводит к пустому обновлению.
- Изменение обнаруженного списка не меняет Grafana alert scopes. Для мониторинга нового pipeline алертами расширить их отдельно.

Офлайн проверки:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
npm install --prefix tests
npm test --prefix tests
```

Первый запуск на реальных API и review naming convention обязательны: одинаковые названия сами по себе не доказывают бизнес-связь систем.

Источники: [TeamCity buildTypes](https://www.jetbrains.com/help/teamcity/rest/buildtypes.html), [TeamCity parameters](https://www.jetbrains.com/help/teamcity/rest/manage-build-configuration-details.html), [Jenkins Remote API](https://www.jenkins.io/doc/book/using/remote-access-api/), [Octopus Projects](https://octopus.com/docs/api/projects), [Octopus Environments](https://octopus.com/docs/api/environments).
