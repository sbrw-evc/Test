"""Generate snapshot dashboard and matching rules from the implemented adapter contract."""
from pathlib import Path
import itertools
import json
import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'cicd-health'
DS = {'type': 'prometheus', 'uid': '${DS_PROMETHEUS}'}
S = 'env=~"$env",component=~"$component"'
O = S + ',project=~"$project"'
FRESH = ' and on (env, component) (ci:data_stale == 0) and on (env, component) (ci:component_available == 1)'
panels, ids = [], itertools.count(1)
status = [{'type': 'value', 'options': {str(k): {'text': v, 'color': c} for k,v,c in [(0,'DOWN','red'),(1,'UNKNOWN','gray'),(2,'DEGRADED','orange'),(3,'HEALTHY','green')]}}]
outcomes = [{'type': 'value', 'options': {str(k): {'text': v, 'color': c} for k,v,c in [(0,'SUCCESS','green'),(1,'UNSTABLE','orange'),(2,'FAILED','red'),(3,'CANCELED','gray'),(4,'TIMED OUT','red'),(5,'UNKNOWN','gray')]}}]

def panel(title, typ, x,y,w,h, expr=None, unit='short', desc='', mappings=None, legend='{{component}}'):
    p = {'id':next(ids),'title':title,'type':typ,'gridPos':{'x':x,'y':y,'w':w,'h':h},'description':desc,
         'datasource':DS,'targets':[] if expr is None else [{'refId':'A','expr':expr,'legendFormat':legend,'instant':typ in ('stat','table','bargauge'),'range':typ=='timeseries','format':'table' if typ=='table' else 'time_series','datasource':DS}],
         'fieldConfig':{'defaults':{'unit':unit,'noValue':'NO DATA','mappings':mappings or [],'color':{'mode':'thresholds' if mappings else 'palette-classic'},'thresholds':{'mode':'absolute','steps':[{'color':'green','value':None}]}},'overrides':[]},'options':{}}
    if typ=='stat':
        p['options']={'reduceOptions':{'calcs':['lastNotNull'],'fields':'','values':False},'graphMode':'none','colorMode':'value','textMode':'auto'}
    if typ=='timeseries':
        p['options']={'legend':{'displayMode':'table','placement':'bottom','calcs':['lastNotNull','max']},'tooltip':{'mode':'multi','sort':'desc'}}
        p['fieldConfig']['defaults']['custom']={'lineWidth':2,'fillOpacity':8,'spanNulls':False,'drawStyle':'line'}
    if typ=='table':
        p['options']={'showHeader':True,'cellHeight':'sm'}
        p['fieldConfig']['overrides']=[{'matcher':{'id':'byName','options':'Time'},'properties':[{'id':'custom.hidden','value':True}]}]
    if typ=='bargauge':
        p['options']={'orientation':'horizontal','displayMode':'gradient','reduceOptions':{'calcs':['lastNotNull'],'fields':'','values':False}}
    panels.append(p)
    return p

p=panel('CI/CD Operations', 'text',0,0,24,3)
p['options']={'mode':'markdown','content':'### CI/CD HEALTH\nTeamCity · Jenkins · Octopus | DOWN → UNKNOWN → DEGRADED → HEALTHY\n\nДанные выбранных pipeline. Доля успеха относится к **последним результатам**, не к запускам за период. Устаревшие KPI скрыты.'}
for key in ('datasource','fieldConfig','targets'):p.pop(key)
for i,(component,title) in enumerate([('teamcity','TeamCity'),('jenkins','Jenkins'),('octopus','Octopus')]):
    p=panel(title,'stat',i*6,3,6,5,f'ci:operational_state{{env=~"$env",component="{component}"}}',mappings=status,
            desc='DOWN: проверка HTTP/API неуспешна. UNKNOWN: сбор отсутствует, старше 180 s или неполный. DEGRADED: последний результат неуспешен, очередь или агенты ниже порога.')
    p['links']=[{'title':'Открыть '+title,'url':'$'+component+'_url','targetBlank':True}]
    p['fieldConfig']['defaults']['links']=[{'title':'Детали компонента','url':f'/d/cicd-health?var-env=${{env}}&var-component={component}&var-project=All&from=${{__from}}&to=${{__to}}'}]
panel('Общее состояние','stat',18,3,6,5,'min(ci:operational_state{env=~"$env"})',mappings=status,desc='Худший статус всех трёх ожидаемых систем; component фильтр не влияет.')
for i,(title,operation) in enumerate([('Успешны последние сборки','build'),('Успешны последние деплои','deploy')]):
    expr=f'100 * avg((ci_last_operation_status{{{O},operation="{operation}"}} == bool 0){FRESH})'
    p=panel(title,'stat',i*6,8,6,4,expr,'percent',desc='Один последний завершённый результат на каждый выбранный pipeline. Pipeline без запусков отсутствуют в знаменателе. Не процент за выбранный период.')
    p['fieldConfig']['defaults'].update(min=0,max=100,decimals=1)
panel('Агенты онлайн TC и Jenkins','stat',12,8,6,4,f'sum(ci_agents_online{{{S},component=~"teamcity|jenkins"}}{FRESH})',desc='TC: connected + authorized + enabled. Jenkins: online с executors > 0, без контроллера. Это не число свободных совместимых слотов. Без фильтра project.')
panel('Очередь выбранных pipeline','stat',18,8,6,4,f'sum(ci_queue_length{{{S}}}{FRESH})',desc='Scope задаётся config.yml адаптера. Project фильтр не меняет эту очередь.')
panel('Очереди','timeseries',0,12,12,7,f'ci_queue_length{{{S}}}{FRESH}')
panel('HTTP время ответа','timeseries',12,12,12,7,f'probe_duration_seconds{{{S},job="cicd-http"}} and (time()-timestamp(probe_duration_seconds{{{S},job="cicd-http"}}) < 90)','s',desc='Полная длительность blackbox HTTP probe, включая DNS/TCP/TLS.')
panel('Длительность последней операции','timeseries',0,19,12,7,f'ci_last_operation_duration_seconds{{{O}}}{FRESH}','s',legend='{{component}} / {{pipeline}}',desc='Ступенчатое значение последней завершённой операции, не p95 и не распределение всех запусков.')
panel('Текущие операции','timeseries',12,19,12,7,f'ci_running_elapsed_seconds{{{O}}}{FRESH}','s',legend='{{component}} / {{pipeline}}',desc='Максимальный возраст текущей операции на pipeline; ноль если нет активных.')
panel('Последние результаты','table',0,26,14,9,f'ci_last_operation_status{{{O}}}{FRESH}',mappings=outcomes,desc='Для Octopus: последняя по порядку создания завершённая Deploy task в project/environment. Runbook, системные tasks и другие environments исключены.')
panel('Возраст самого старого задания','bargauge',14,26,10,5,f'ci_queue_oldest_age_seconds{{{S}}}{FRESH}','s')
p=panel('Возраст полного сбора','bargauge',14,31,10,4,f'ci:data_age_seconds{{{S}}}','s')
p['fieldConfig']['defaults'].update(color={'mode':'thresholds'},thresholds={'mode':'absolute','steps':[{'color':'green','value':None},{'color':'orange','value':120},{'color':'red','value':180}]})
panel('Последняя операция завершена','table',0,35,14,7,f'ci_last_operation_completed_timestamp_seconds{{{O}}} * 1000{FRESH}','dateTimeAsIso')
panel('Ожидаемые постоянные агенты','table',14,35,10,7,f'ci_resource_online{{{S}}}{FRESH}',mappings=[{'type':'value','options':{'0':{'text':'OFFLINE','color':'red'},'1':{'text':'ONLINE','color':'green'}}}],desc='Заполнить expected_agents. Ephemeral Kubernetes agents не включать. Пустая таблица допустима.')
for i,(component,expr) in enumerate([('teamcity','agents_connected_authorized_number'),('jenkins','default_jenkins_executors_idle')]):
    panel(component+' нативная метрика','timeseries',i*8,42,8,6,f'{expr}{{env=~"$env",component="{component}"}}',desc='Диагностика нативного источника; проверить фактическое имя метрики установленной версии.')
panel('Octopus доступность API','timeseries',16,42,8,6,'ci_api_up{env=~"$env",component="octopus"}',desc='Адаптер выполняет только GET tasks. Worker capacity не оценивается.')
p={'id':next(ids),'type':'alertlist','title':'Активные алерты CI/CD','gridPos':{'x':0,'y':48,'w':24,'h':7},'options':{'showOptions':'current','maxItems':20,'sortOrder':1,'dashboardAlerts':False,'alertName':'CI/CD','stateFilter':{'firing':True,'pending':True,'error':True,'noData':True,'normal':False},'groupMode':'default'}}
panels.append(p)
variables=[{'name':'DS_PROMETHEUS','type':'datasource','query':'prometheus','current':{'text':'Prometheus','value':'cicd-prometheus'}},
           {'name':'env','label':'Окружение','type':'query','datasource':DS,'query':'label_values(ci:expected_component, env)','refresh':1,'current':{'text':'prod','value':'prod'}},
           {'name':'component','label':'Компоненты','type':'custom','query':'teamcity,jenkins,octopus','multi':True,'includeAll':True,'allValue':'.*','current':{'text':'All','value':'$__all'}},
           {'name':'project','label':'Проект только операции','type':'query','datasource':DS,'query':'label_values(ci_operation_timeout_seconds{env=~"$env",component=~"$component"}, project)','multi':True,'includeAll':True,'allValue':'.*','refresh':1,'current':{'text':'All','value':'$__all'}}]
for c in ('teamcity','jenkins','octopus'):
    variables.append({'name':c+'_url','type':'constant','query':'https://'+c+'.example.internal','hide':2})
dashboard={'id':None,'uid':'cicd-health','title':'CI/CD Health · Operations','schemaVersion':39,'version':2,'editable':True,'style':'dark','tags':['cicd','operations'],'timezone':'browser','time':{'from':'now-6h','to':'now'},'refresh':'30s','templating':{'list':variables},'panels':panels,'annotations':{'list':[]},'links':[],'description':'Snapshot MVP backed by adapter/exporter.py. No event counters, p95 or inferred Octopus capacity.'}
(OUT/'cicd-health-dashboard.json').write_text(json.dumps(dashboard,ensure_ascii=False,indent=2)+'\n')

records=[{'record':'ci:expected_component','expr':'vector(1)','labels':{'env':'prod','component':c}} for c in ('teamcity','jenkins','octopus')]
def rec(name,expr): records.append({'record':name,'expr':expr})
probe='max by (env, component) (probe_success{job="cicd-http"} and (time()-timestamp(probe_success{job="cicd-http"}) < 90))'
api='max by (env, component) (ci_api_up and (time()-timestamp(ci_api_up) < 90))'
rec('ci:probe_up',probe+' or on (env, component) ci:expected_component')
rec('ci:api_up',api+' or on (env, component) ci:expected_component')
rec('ci:observation_missing','(ci:expected_component unless on (env, component) ('+probe+')) or on (env, component) (ci:expected_component unless on (env, component) ('+api+')) or on (env, component) (0 * ci:expected_component)')
rec('ci:data_age_seconds','(time() - max by (env, component) (ci_collection_last_success_timestamp_seconds)) or on (env, component) (1000000000 * ci:expected_component)')
rec('ci:collector_healthy','max by (env, component) (ci_collection_complete) or on (env, component) (0 * ci:expected_component)')
rec('ci:component_available','ci:probe_up * on (env, component) ci:api_up')
rec('ci:data_stale','clamp_max((ci:data_age_seconds > bool 180) + on (env, component) (ci:collector_healthy == bool 0) + on (env, component) ci:observation_missing, 1)')
branches=['max by (env, component) (ci_queue_oldest_age_seconds > bool 300)',
          'max by (env, component) (ci_queue_length > bool ci_queue_limit)',
          'max by (env, component) (ci_resource_online == bool 0)',
          'max by (env, component) (ci_agents_online < bool ci_agents_minimum)',
          'max by (env, component) (ci_last_operation_status > bool 0)']
rec('ci:degraded','clamp_max('+' + on (env, component) '.join('('+e+' or on (env, component) (0 * ci:expected_component))' for e in branches)+', 1)')
rec('ci:operational_state','ci:component_available * on (env, component) (3 - 2 * ci:data_stale - (1 - ci:data_stale) * on (env, component) ci:degraded)')
groups=[{'name':'cicd-normalized','interval':'30s','rules':records}]
(OUT/'prometheus-recording-rules.yml').write_text(yaml.safe_dump({'groups':groups},sort_keys=False))
(ROOT/'kubernetes/recording-rules.yml').write_text(yaml.safe_dump({'apiVersion':'monitoring.coreos.com/v1','kind':'PrometheusRule','metadata':{'name':'cicd-health','namespace':'monitoring','labels':{'release':'kube-prometheus-stack'}},'spec':{'groups':groups}},sort_keys=False))

rules=[]
def alert(uid,title,expr,hold,severity,summary):
    rules.append({'uid':uid,'title':'CI/CD · '+title,'condition':'B','for':hold,'noDataState':'OK','execErrState':'Error','isPaused':False,'labels':{'domain':'cicd','severity':severity,'team':'platform'},'annotations':{'summary':summary,'__dashboardUid__':'cicd-health','__panelId__':'2'},'data':[
        {'refId':'A','datasourceUid':'cicd-prometheus','relativeTimeRange':{'from':600,'to':0},'model':{'refId':'A','expr':expr,'instant':True,'range':False,'format':'time_series','datasource':{'type':'prometheus','uid':'cicd-prometheus'}}},
        {'refId':'B','datasourceUid':'__expr__','relativeTimeRange':{'from':0,'to':0},'model':{'refId':'B','type':'math','expression':'$A > 0','datasource':{'type':'__expr__','uid':'__expr__'}}}]})
alert('cicd-server-down','HTTP or API unavailable','1 - ci:component_available','2m','critical','Observed HTTP/API check failed. API failure may also indicate credentials, permissions or an unavailable pipeline.')
alert('cicd-data-stale','Data stale or incomplete','ci:data_stale','3m','warning','No complete snapshot within 180 s, missing probe/exporter, or incomplete API pagination.')
for operation,severity in [('build','warning'),('deploy','critical')]:
    alert('cicd-'+operation+'-failed','Last '+operation+' failed',f'(ci_last_operation_status{{operation="{operation}"}} == 2 or ci_last_operation_status{{operation="{operation}"}} == 4)'+FRESH,'0s',severity,'Latest observed terminal result is failed or timed out. Resolves when a later terminal result replaces it. Polling can miss intermediate failures.')
alert('cicd-agent-down','Expected agent unavailable','(1 - ci_resource_online)'+FRESH,'5m','warning','Explicitly expected stable agent is missing, disconnected or disabled.')
alert('cicd-agents-low','Too few online agents','(ci_agents_online < bool ci_agents_minimum)'+FRESH,'5m','warning','Online agent count below configured minimum. Not a compatibility or free-slot guarantee.')
alert('cicd-queue-size','Queue exceeds limit','(ci_queue_length > bool ci_queue_limit)'+FRESH,'10m','warning','Selected-pipeline queue above configured limit.')
alert('cicd-queue-age','Queue waiting too long','(ci_queue_oldest_age_seconds > bool 900)'+FRESH,'5m','critical','Oldest queued operation waiting longer than 15 min.')
alert('cicd-long-build','Build running too long','(ci_running_elapsed_seconds{operation="build"} > bool ci_operation_timeout_seconds{operation="build"})'+FRESH,'5m','warning','Current build exceeds configured timeout.')
alert('cicd-slow-http','HTTP response too slow','(probe_duration_seconds{job="cicd-http"} > bool 2) and (probe_success{job="cicd-http"} == 1)','5m','warning','HTTP probe exceeds 2 s.')
alert('cicd-rules-missing','Recording rules missing',' or '.join(f'absent(ci:expected_component{{env="prod",component="{c}"}})' for c in ('teamcity','jenkins','octopus')),'2m','critical','Expected component inventory rules missing.')
(OUT/'grafana-alert-rules.yml').write_text(yaml.safe_dump({'apiVersion':1,'deleteRules':[{'orgId':1,'uid':u} for u in ('cicd-capacity','cicd-native-scrape')],'groups':[{'orgId':1,'name':'CI-CD Health','folder':'CI-CD Operations','interval':'30s','rules':rules}]},sort_keys=False,allow_unicode=True))
print(f'Generated {len(panels)} panels and {len(rules)} alerts')
