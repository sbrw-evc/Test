# Контракт рабочего Grafana v2 dashboard

Infinity JQ backend получает CI/CD REST snapshots. Prometheus отвечает за pod/container/PVC history. Текущий источник настроек — переданный рабочий экспорт; datasource UID и endpoints сохранены в credential-free profile.

- Health 2 KPI NORMAL, 1 DEGRADED: queue>10, failures в последних100 за10m, age>1800s (3600s Octopus), unexpected offline agents; TC/Jenkins additionally available<1. 0 UNAVAILABLE возможен только при наблюдаемом availability=0; HTTP/schema Error остаётся Error/No data, не кодируется в 0 или success. Причины берутся из тех же запросов.
- Success sample не является success rate за timepicker interval. Cancelled/aborted excluded; Jenkins UNSTABLE considered unsuccessful. Пустой history не становится 100%.
- Completed tables: failures first, clickable run ID, per-row branch/release context and actor. Не переданные metadata не выдумываются. TaskId → deployments lookup ограничен последними1000; Version может быть заменён точным ReleaseId. LastModifiedBy не считается trigger actor.
- Current age сравнивается с mean successful duration того же workflow при минимум3 observations. Jenkins scheduled timestamp includes wait; executor map may omit invisible/transient runs. TC current agent comes from agent.name/id.
- Queue: count, max waiting age, unique reasons; missing queue timestamp leaves unknown age.
- Agents: TC busy по assigned build; Jenkins busy agent по idle=false, не busy executor count. Octopus здоровый worker не равно idle worker; busy allocation неизвестна и так подписана.
- Ranking: failures, mean/max duration, sample size and alternation heuristic (≥4 binary results, ≥50% transitions). Unknown Octopus project identity excluded, not grouped under generic Deploy.
- Prod scope берётся из real environment options. Recent Prod per project remains bounded latest100 tasks; не гарантирует все projects. 24h failure API запрос получает нижнюю границу от inline backend clock и фильтрует completed interval, take1000 with truncation check; jq на каждом refresh отсекает finish старше now-24h. Окно независимо от timepicker.
- Pods всегда ограничены двумя CI namespace. Legend maps TeamCity/Jenkins/Octopus/Nexus/Tentacle prefixes; unknown pods retain raw name. Requests/limits — суммы контейнеров, не effective Pod-level resource semantics.
- PVC percent = deduplicated used/capacity, denominator>0; warning80%, critical90%. Estimated time-to-full = available / negative derivative6h. predict_linear available6h→7d — отдельный forecast. No slope/no metrics = No data, не «место не закончится».
- Annotations: restart counter increase in5m observation windows; Prod completion times from API. API inventory limit and selected time bounds apply.

Live permissions/schema validation in Grafana is required. Fixed alert examples are independent of UI filters. No custom exporter and no fabricated HTTP latency/source freshness signal.
