"""Offline config generator. No runtime service and no custom exporter."""
from pathlib import Path
import copy, itertools, json, sys
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl, quote
import yaml

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'cicd-health'
C=yaml.safe_load(Path(sys.argv[1] if len(sys.argv)>1 else ROOT/'monitoring-config.example.yml').read_text())
DASHBOARD_ONLY='--dashboard-only' in sys.argv[2:]
ENV=C['env']; E='env='+json.dumps(ENV)
PROM={'type':'prometheus','uid':'cicd-prometheus'}
TC={'type':'yesoreyeram-infinity-datasource','uid':'teamcity-api'}
OC={'type':'yesoreyeram-infinity-datasource','uid':'octopus-api'}
J='job="jenkins",'+E
JOBS=J+',jenkins_job=~'+json.dumps(C['jenkins_job_regex'])
NODES=J+',node=~'+json.dumps(C['jenkins_node_regex'])
POOL=J+',label='+json.dumps(C['jenkins_capacity_label'])
queries={}

def infinity(name,ds,path,params,root):
    base=C['teamcity_url'] if ds==TC else C['octopus_url']
    q={'refId':'A','type':'json','source':'url','parser':'backend','format':'table',
       'url':base.rstrip('/')+'/'+path.lstrip('/')+'?'+urlencode(params),
       'url_options':{'method':'GET'},'root_selector':root,'columns':[],
       'datasource':ds,'pagination_mode':'none'}
    queries[name]=q
    return q

def pquery(expr,ref='A',hide=False):
    return {'refId':ref,'expr':expr,'instant':True,'range':False,'format':'time_series','datasource':PROM,'hide':hide}

def math(expr,ref='B',hide=False):
    return {'refId':ref,'type':'math','expression':expr,'datasource':{'type':'__expr__','uid':'__expr__'},'hide':hide}

# TeamCity compact timestamp converted to ISO8601 with its explicit timezone.
DATE='$date := function($s){$toMillis($substring($s,0,4)&"-"&$substring($s,4,2)&"-"&$substring($s,6,2)&"T"&$substring($s,9,2)&":"&$substring($s,11,2)&":"&$substring($s,13,2)&$substring($s,15,3)&":"&$substring($s,18,2))};'
TGUARD='$exists(count) ? true : $error("Invalid TeamCity response");'
OGUARD='$exists(TotalResults) and $exists(Items) ? true : $error("Invalid Octopus response");'
TBASE={'locator':f'buildType:(id:{C["teamcity_build_type"]}),state:finished,defaultFilter:false,count:1',
       'fields':'count,build(id,status,canceledInfo,startDate,finishDate,webUrl)'}
TSTATUS='count=0 ? -1 : (($exists(build[0].canceledInfo) and build[0].canceledInfo!=null) ? 3 : build[0].status="SUCCESS" ? 0 : build[0].status="FAILURE" ? 2 : 5)'
infinity('tc_status',TC,'app/rest/builds',TBASE,'('+TGUARD+TSTATUS+')')
infinity('tc_health',TC,'app/rest/builds',TBASE,'('+TGUARD+'count=0 ? 1 : ('+TSTATUS+')=0 ? 3 : ('+TSTATUS+')=5 ? 1 : 2)')
infinity('tc_failed',TC,'app/rest/builds',TBASE,'('+TGUARD+'count>0 and build[0].status="FAILURE" and ($not($exists(build[0].canceledInfo)) or build[0].canceledInfo=null) ? 1 : 0)')
infinity('tc_duration',TC,'app/rest/builds',TBASE,'('+TGUARD+DATE+'count=0 or $not($exists(build[0].startDate)) or build[0].startDate=null ? -1 : ($date(build[0].finishDate)-$date(build[0].startDate))/1000)')
TPAR={'locator':f'buildType:(id:{C["teamcity_build_type"]}),state:running,defaultFilter:false,count:100','fields':'count,nextHref,build(id,startDate)'}
infinity('tc_running',TC,'app/rest/builds',TPAR,'('+TGUARD+DATE+'$exists(nextHref) ? $error("Running build page truncated") : count=0 ? 0 : $max(build.($millis()-$date(startDate)))/1000)')
infinity('tc_agent',TC,'app/rest/agents',{'locator':f'id:{C["teamcity_expected_agent"]},defaultFilter:false','fields':'count,agent(id,connected,authorized,enabled)'},'('+TGUARD+'count>0 and agent[0].connected and agent[0].authorized and agent[0].enabled ? 1 : 0)')
OBASE={'name':'Deploy','project':C['octopus_project'],'environment':C['octopus_environment']}
OPATH='api/'+C['octopus_space']+'/tasks'
OTERM=dict(OBASE,active='false',take=1)
OSTATUS='TotalResults=0 ? -1 : Items[0].State="Success" ? 0 : Items[0].State="Failed" ? 2 : Items[0].State="Canceled" ? 3 : Items[0].State="TimedOut" ? 4 : 5'
infinity('oc_status',OC,OPATH,OTERM,'('+OGUARD+OSTATUS+')')
infinity('oc_health',OC,OPATH,OTERM,'('+OGUARD+'TotalResults=0 ? 1 : ('+OSTATUS+')=0 ? 3 : ('+OSTATUS+')=5 ? 1 : 2)')
infinity('oc_failed',OC,OPATH,OTERM,'('+OGUARD+'TotalResults>0 and Items[0].State in ["Failed","TimedOut"] ? 1 : 0)')
infinity('oc_duration',OC,OPATH,OTERM,'('+OGUARD+'TotalResults=0 or $not($exists(Items[0].StartTime)) or Items[0].StartTime=null ? -1 : ($toMillis(Items[0].CompletedTime)-$toMillis(Items[0].StartTime))/1000)')
infinity('oc_queue',OC,OPATH,dict(OBASE,states='Queued',take=1),'('+OGUARD+'TotalResults)')
infinity('oc_api',OC,OPATH,dict(OBASE,take=1),'('+OGUARD+'1)')
infinity('oc_running',OC,OPATH,dict(OBASE,running='true',take=100),'('+OGUARD+'TotalResults>$count(Items) ? $error("Running deployment page truncated") : TotalResults=0 ? 0 : $max(Items.($millis()-$toMillis(StartTime)))/1000)')
if not DASHBOARD_ONLY:(OUT/'infinity-queries.json').write_text(json.dumps(queries,ensure_ascii=False,indent=2)+'\n')

# Prometheus recordings use real native metrics only. No invented ci_* exporter gauges.
records=[{'record':'ci:expected_component','expr':'vector(1)','labels':{'env':ENV,'component':c}} for c in ('teamcity','jenkins','octopus')]
def rec(name,expr,component=None):
    r={'record':name,'expr':expr}
    if component:r['labels']={'env':ENV,'component':component}
    records.append(r)
rec('ci:agents_online',f'max(agents_connected_authorized_number{{job="teamcity",{E}}})','teamcity')
rec('ci:agents_online',f'sum(default_jenkins_nodes_online{{{NODES}}})','jenkins')
rec('ci:queue_length',f'max(builds_queued_number{{job="teamcity",{E}}})','teamcity')
rec('ci:queue_length',f'max(default_jenkins_executors_queue_length{{{POOL}}})','jenkins')
for component in ('teamcity','jenkins'):
    rec('ci:native_up',f'min(up{{job="{component}",{E}}} and (time()-timestamp(up{{job="{component}",{E}}}) < 90))',component)
rec('ci:http_observed','min by (env, component) (probe_success{job="cicd-http"} and (time()-timestamp(probe_success{job="cicd-http"}) < 90))')
rec('ci:http_up','ci:http_observed or on (env,component) ci:expected_component')
rec('ci:source_up','ci:native_up or on (env,component) ci:expected_component')
rec('ci:observed_down','clamp_max((1-ci:http_up) + on (env,component) (1-ci:source_up),1)')
required='(ci:expected_component{component!="octopus"} unless on (env,component) ci:native_up) or on (env,component) (ci:expected_component{component!="octopus"} unless on (env,component) ci:agents_online) or on (env,component) (ci:expected_component{component!="octopus"} unless on (env,component) ci:queue_length)'
rec('ci:observation_missing','(ci:expected_component unless on (env,component) ci:http_observed) or on (env,component) ('+required+') or on (env,component) (0*ci:expected_component)')
rec('ci:degraded',f'((ci:queue_length{{component="teamcity"}} > bool {C["teamcity_queue_limit"]}) + on (env,component) (ci:agents_online{{component="teamcity"}} < bool {C["teamcity_min_agents"]})) > bool 0','teamcity')
rec('ci:degraded',f'((ci:queue_length{{component="jenkins"}} > bool {C["jenkins_queue_limit"]}) + on (env,component) (ci:agents_online{{component="jenkins"}} < bool {C["jenkins_min_agents"]})) > bool 0','jenkins')
rec('ci:infrastructure_state','(1-ci:observed_down) * on (env,component) (3-2*ci:observation_missing-(1-ci:observation_missing) * on (env,component) (ci:degraded or on (env,component) (0*ci:expected_component)))')
# Node/request scrape freshness is observed; async Jenkins internal collection freshness is NOT inferred.
groups=[{'name':'cicd-native','interval':'30s','rules':records}]
if not DASHBOARD_ONLY:(OUT/'prometheus-recording-rules.yml').write_text(yaml.safe_dump({'groups':groups},sort_keys=False))
if not DASHBOARD_ONLY:(ROOT/'kubernetes/recording-rules.yml').write_text(yaml.safe_dump({'apiVersion':'monitoring.coreos.com/v1','kind':'PrometheusRule','metadata':{'name':'cicd-health','namespace':'monitoring','labels':{'release':'kube-prometheus-stack'}},'spec':{'groups':groups}},sort_keys=False))

ids=itertools.count(1); panels=[]
state=[{'type':'value','options':{str(k):{'text':v,'color':c} for k,v,c in [(0,'DOWN','red'),(1,'UNKNOWN','gray'),(2,'DEGRADED','orange'),(3,'HEALTHY','green')]}}]
result=[{'type':'value','options':{str(k):{'text':v,'color':c} for k,v,c in [(-1,'NO RUNS','gray'),(0,'SUCCESS','green'),(1,'UNSTABLE','orange'),(2,'FAILED','red'),(3,'CANCELED','gray'),(4,'TIMED OUT','red'),(5,'UNKNOWN','gray')]}}]
def panel(title,typ,x,y,w,h,targets=None,unit='short',desc='',mappings=None):
    p={'id':next(ids),'type':typ,'title':title,'gridPos':dict(x=x,y=y,w=w,h=h),'description':desc,'datasource':{'type':'datasource','uid':'-- Mixed --'},'targets':targets or [],'fieldConfig':{'defaults':{'unit':unit,'noValue':'UNKNOWN','mappings':mappings or [],'color':{'mode':'thresholds' if mappings else 'palette-classic'},'thresholds':{'mode':'absolute','steps':[{'color':'green','value':None}]}},'overrides':[]},'options':{}}
    if typ=='stat':p['options']={'reduceOptions':{'calcs':['lastNotNull'],'fields':'','values':False},'colorMode':'value','graphMode':'none','textMode':'auto'}
    if typ=='timeseries':
        for t in p['targets']:
            if 'expr' in t:t.update(instant=False,range=True,legendFormat='{{component}} {{jenkins_job}}')
        p['options']={'legend':{'displayMode':'table','placement':'bottom','calcs':['lastNotNull','max']},'tooltip':{'mode':'multi'}}
        p['fieldConfig']['defaults']['custom']={'drawStyle':'line','lineWidth':2,'fillOpacity':8,'spanNulls':False}
    if typ=='table':p['options']={'showHeader':True,'cellHeight':'sm'}
    panels.append(p);return p

def inf(name,ref='A',hide=False):
    q=copy.deepcopy(queries[name]);q.update(refId=ref,hide=hide);return q

def operational(component,health=None):
    q=[pquery(f'min(ci:infrastructure_state{{{E},component="{component}"}})', 'P', bool(health))]
    if health:q += [inf(health,'S',True),math('($P+$S-abs($P-$S))/2','H')]
    return q
p=panel('CI/CD Operations','text',0,0,24,3)
p['options']={'mode':'markdown','content':'### CI/CD HEALTH · OPEN SOURCE COLLECTION\nTeamCity native · Jenkins Prometheus Plugin · Octopus REST / Infinity · Blackbox\n\nСостояние выбранного scope. HTTP error/No data не означает HEALTHY. REST KPI — текущий снимок, без истории в Prometheus.'}
for key in ('datasource','fieldConfig','targets'):p.pop(key)
for i,(c,h) in enumerate([('teamcity','tc_health'),('jenkins',None),('octopus','oc_health')]):
    targets=operational(c,h)
    if c=='jenkins':
        targets[0]['hide']=True
        targets += [pquery(f'(max(default_jenkins_builds_last_build_result_ordinal{{{JOBS}}} > bool 0) )','S',True),math('$P-($P==3)*$S','H')]
    p=panel(c.title(),'stat',i*6,3,6,5,targets,desc='Infrastructure status + last result в настроенном scope. Ошибка Infinity query показывает error/UNKNOWN; не подставляем успех.',mappings=state)
    p['links']=[{'title':'Открыть '+c,'url':C[c+'_url'],'targetBlank':True},{'title':'Детали в Grafana','url':f'/d/cicd-health/cicd?viewPanel={19+i}','targetBlank':False}]
# Overall mixed-source numeric inputs carry no labels, so expression joins are deterministic.
panel('Общее здоровье scope','stat',18,3,6,5,[pquery(f'min(ci:infrastructure_state{{{E}}})','P',True),inf('tc_health','T',True),inf('oc_health','O',True),pquery(f'max(default_jenkins_builds_last_build_result_ordinal{{{JOBS}}} > bool 0)','J',True),math('$P-($P==3)*$J','I',True),math('($I+$T-abs($I-$T))/2','M',True),math('($M+$O-abs($M-$O))/2','H')],mappings=state,desc='Минимум infrastructure state всех трёх систем и последних результатов выбранных TC/Jenkins/Octopus scopes. No data/error REST не заменяются healthy.')
panel('TeamCity последняя сборка','stat',0,8,8,4,[inf('tc_status')],mappings=result)
panel('Jenkins последние сборки','stat',8,8,8,4,[pquery(f'default_jenkins_builds_last_build_result_ordinal{{{JOBS}}}')],mappings=[{'type':'value','options':{'0':{'text':'SUCCESS','color':'green'},'1':{'text':'UNSTABLE','color':'orange'},'2':{'text':'FAILED','color':'red'},'3':{'text':'NOT BUILT','color':'gray'},'4':{'text':'ABORTED','color':'gray'}}}],desc='Ordinal: 0 success 1 unstable 2 failure 3 not built 4 aborted. Словарь кодов Jenkins отличается от Octopus.')
panel('Octopus последний deploy','stat',16,8,8,4,[inf('oc_status')],mappings=result)
panel('TeamCity подключённые агенты','stat',0,12,6,4,[pquery(f'ci:agents_online{{{E},component="teamcity"}}')],desc='Connected + authorized; не число свободных/enabled/совместимых агентов.')
panel('Jenkins online agents','stat',6,12,6,4,[pquery(f'ci:agents_online{{{E},component="jenkins"}}')],desc='Node status, заданный node regex; controller исключить.')
panel('Jenkins idle executors','stat',12,12,6,4,[pquery(f'max(default_jenkins_executors_idle{{{POOL}}})')],desc='Один label pool; labels пересекаются, их нельзя суммировать.')
panel('Octopus queued deployments','stat',18,12,6,4,[inf('oc_queue')],desc='TotalResults для states=Queued, Deploy, project+environment; не count первой страницы.')
panel('Очереди TC и Jenkins','timeseries',0,16,12,7,[pquery(f'ci:queue_length{{{E}}}')],desc='TC whole server; Jenkins selected label waiting for executor.')
panel('HTTP время ответа','timeseries',12,16,12,7,[pquery(f'probe_duration_seconds{{job="cicd-http",{E}}}')],'s')
panel('TeamCity длительность последней','stat',0,23,8,4,[inf('tc_duration')],'s',desc='REST start→finish; -1 нет запуска/начала.',mappings=[{'type':'value','options':{'-1':{'text':'NO RUNS','color':'gray'}}}])
panel('Jenkins длительность последней','stat',8,23,8,4,[pquery(f'default_jenkins_builds_last_build_duration_milliseconds{{{JOBS}}}/1000')],'s')
panel('Octopus длительность последнего','stat',16,23,8,4,[inf('oc_duration')],'s',desc='REST start→finish; -1 нет начала или запуска.',mappings=[{'type':'value','options':{'-1':{'text':'NO RUNS','color':'gray'}}}])
panel('TeamCity самая долгая текущая','stat',0,27,8,4,[inf('tc_running')],'s',desc='По выбранному buildType; >100 running возвращает query error, не частичное значение.')
panel('TeamCity детали','table',0,31,8,8,[inf('tc_agent')],desc='Ожидаемый постоянный ID '+str(C['teamcity_expected_agent'])+'; 1 connected+authorized+enabled, 0 отсутствует/offline/disabled.')
panel('Jenkins детали','table',8,31,8,8,[pquery(f'default_jenkins_nodes_online{{{NODES}}}')],desc='1 online 0 offline; фильтр permanent nodes. Графики и statuses зависят от async collection plugin.')
panel('Octopus детали','table',16,31,8,8,[inf('oc_running')],'s',desc='Максимальный возраст active deployment; >100 running => query error. Workers/targets capacity не собирается.')
panel('Jenkins самая долгая текущая','stat',8,27,8,4,[pquery(f'max(default_jenkins_builds_running_build_duration_milliseconds{{{JOBS}}})/1000')],'s')
panel('Octopus доступность REST','stat',16,27,8,4,[inf('oc_api')],desc='1 при валидном live ответе; HTTP/auth/schema error отображается ошибкой. Не превращается в 0/успех автоматически.')
panel('Infrastructure state и пропуски','timeseries',0,39,12,7,[pquery(f'ci:infrastructure_state{{{E}}}')],desc='0 down 1 missing observation 2 degraded 3 healthy. Только native+HTTP; REST state в верхних mixed-source cards.')
panel('Jenkins failure rate','timeseries',12,39,12,7,[pquery(f'sum(rate(default_jenkins_builds_failed_build_count{{{JOBS}}}[5m]))')],'ops',desc='Нативный counter Jenkins, не TC/Octopus. Редкие события могут появляться с задержкой внутренней collection.')
panels.append({'id':next(ids),'type':'alertlist','title':'CI/CD активные алерты','gridPos':dict(x=0,y=46,w=24,h=7),'options':{'showOptions':'current','maxItems':20,'alertName':'CI/CD','stateFilter':{'firing':True,'pending':True,'error':True,'noData':True,'normal':False}}})
# Dashboard templates are isolated from fixed recording/alert queries above/below.
# URLs are encoded once; Grafana percentencode interpolates only the selected ID.
variables=[]
def current(value):return {'text':value,'value':value,'selected':True}
def variable(name,label,typ,value,**extra):
    v={'name':name,'label':label,'type':typ,'hide':0,'skipUrlSync':False,
       'multi':False,'includeAll':False,'current':current(value),'options':[],**extra}
    variables.append(v);return v
variable('server_env','Контур серверов','constant',ENV,query=ENV,skipUrlSync=True,
         description='Контур и адреса серверов заданы при генерации; это информационное поле, не переключатель REST-серверов.')
variable('component','Компоненты графиков','custom','$__all',query='teamcity,jenkins,octopus',multi=True,
         includeAll=True,allValue='.*',options=[{'text':'All','value':'$__all','selected':True}]+
         [{'text':c,'value':c,'selected':False} for c in ('teamcity','jenkins','octopus')],
         current={'text':['All'],'value':['$__all'],'selected':True},
         description='Фильтрует только общие графики HTTP и инфраструктуры. Верхние карточки сохраняют всю цепочку.')

def prom_variable(name,label,metric,field,value,multi=False,regex=''):
    text=f'label_values({metric}, {field})'
    return variable(name,label,'query',value,datasource=PROM,query={'query':text,'refId':'variable'},
                    definition=text,refresh=1,sort=1,regex=regex,multi=multi,includeAll=multi,
                    **({'allValue':None,'current':{'text':['All'],'value':['$__all'],'selected':True}} if multi else {}))
pipeline_definitions=C.get('pipelines') or [{'id':'default','name':'Основной пайплайн',
    'teamcity_build_type':C['teamcity_build_type'],'jenkins_job_regex':C['jenkins_job_regex'],
    'octopus_space':C['octopus_space'],'octopus_project':C['octopus_project'],
    'octopus_environment':C['octopus_environment']}]
import re
assert len({p['id'] for p in pipeline_definitions})==len(pipeline_definitions),'Pipeline IDs must be unique'
for item in pipeline_definitions:
    assert re.fullmatch(r'[A-Za-z0-9_-]+',item['id']),'Pipeline ID must be URL-safe'
    assert ',' not in item['name'] and ':' not in item['name'],'Pipeline name cannot contain comma or colon'
    for field in ('name','teamcity_build_type','jenkins_job_regex','octopus_space','octopus_project','octopus_environment'):
        assert isinstance(item[field],str) and item[field],'Missing pipeline field: '+field
# Inline raw interpolation is inside a PromQL string literal.
for item in pipeline_definitions:
    item['jenkins_job_regex_promql']=json.dumps(item['jenkins_job_regex'])[1:-1]
first=pipeline_definitions[0]
variable('pipeline','Пайплайн','custom',first['id'],
         query=','.join(item['name']+' : '+item['id'] for item in pipeline_definitions),
         current={'text':first['name'],'value':first['id'],'selected':True},
         options=[{'text':item['name'],'value':item['id'],'selected':item==first} for item in pipeline_definitions],
         description='Одновременно выбирает TC buildType, Jenkins job scope и Octopus deployment scope из config.')
# Bound source scopes are derived from one pipeline; independent system overrides
# would make the pipeline label inaccurate, so these variables stay hidden.
for name,field in [('tc_build_type','teamcity_build_type'),('pipeline_jenkins_jobs','jenkins_job_regex_promql'),
                   ('octopus_space','octopus_space'),('octopus_project','octopus_project'),
                   ('octopus_environment','octopus_environment')]:
    model={'refId':'variable','type':'json','source':'inline','parser':'backend','format':'table',
           'data':json.dumps(pipeline_definitions,ensure_ascii=False),
           'root_selector':'[$[id=${pipeline:json}].({"__text":'+field+',"__value":'+field+'})]',
           'columns':[{'selector':k,'text':k,'type':'string'} for k in ('__text','__value')]}
    variable(name,name,'query',first[field],datasource=TC,hide=2,refresh=1,sort=0,regex='',
             query={'refId':'variable','queryType':'infinity','infinityQuery':model})
prom_variable('jenkins_job','Jenkins job',
              f'default_jenkins_builds_last_build_result_ordinal{{{J},jenkins_job=~"${{pipeline_jenkins_jobs:raw}}"}}',
              'jenkins_job','$__all',multi=True)
prom_variable('jenkins_node','Jenkins агент',f'default_jenkins_nodes_online{{{NODES}}}',
              'node','$__all',multi=True)
prom_variable('jenkins_pool','Jenkins пул',f'default_jenkins_executors_idle{{{J}}}',
              'label',C['jenkins_capacity_label']) # Single only: labels overlap.
# Grafana refreshes dependent variables when pipeline changes.
order=['server_env','pipeline','tc_build_type','pipeline_jenkins_jobs','octopus_space',
       'octopus_project','octopus_environment','component','jenkins_job','jenkins_node','jenkins_pool']
variables.sort(key=lambda v:order.index(v['name']))
DJOBS=J+',jenkins_job=~"${jenkins_job:regex}"'
DNODES=J+',node=~"${jenkins_node:regex}"'
DPOOL=J+',label=~"${jenkins_pool:regex}"'

def dashboard_url(q):
    u=urlsplit(q['url']);params=dict(parse_qsl(u.query));path=u.path
    if q['datasource']==TC:
        if 'builds' in path:
            params['locator']=params['locator'].replace('id:'+str(C['teamcity_build_type']),
                                                       'id:${tc_build_type:percentencode}')
    else:
        path=path.replace('/api/'+C['octopus_space']+'/', '/api/${octopus_space:percentencode}/')
        params['project']='${octopus_project:percentencode}'
        params['environment']='${octopus_environment:percentencode}'
    encoded=urlencode(params)
    for name in ('tc_build_type','octopus_project','octopus_environment'):
        token='${'+name+':percentencode}'
        encoded=encoded.replace(quote(token,safe=''),token)
    return urlunsplit((u.scheme,u.netloc,path,encoded,u.fragment))

byid={p['id']:p for p in panels}
for p in panels:
    for t in p.get('targets',[]):
        if 'expr' in t:
            t['expr']=t['expr'].replace(JOBS,DJOBS).replace(NODES,DNODES).replace(POOL,DPOOL)
        elif t.get('source')=='url':t['url']=dashboard_url(t)
# Recording totals are fixed scopes; detail filters use the native series directly.
byid[10]['targets']=[pquery(f'sum(default_jenkins_nodes_online{{{DNODES}}})')]
byid[10]['description']='Online выбранных агентов. Инфраструктурные thresholds верхних cards сохраняют fleet из config.'
byid[13]['targets']=[pquery(f'ci:queue_length{{{E},component="teamcity"}}'),
                      pquery(f'max(default_jenkins_executors_queue_length{{{DPOOL}}})','B')]
for t,legend in zip(byid[13]['targets'],['TeamCity — вся очередь','Jenkins — ${jenkins_pool:text}']):
    t.update(instant=False,range=True,legendFormat=legend)
byid[13]['description']='TeamCity whole server; Jenkins выбранный label pool. Agents/job filters не меняют серверную очередь.'
for pid in (14,24):
    for t in byid[pid]['targets']:
        t['expr']=t['expr'].replace('{'+E+'}', '{'+E+',component=~"${component:regex}"}')
        if pid==14:t['expr']=f'probe_duration_seconds{{job="cicd-http",{E},component=~"${{component:regex}}"}}'
byid[21]['title']='Octopus текущие деплои'
byid[21]['description']='Возраст самой долгой текущей deployment task в выбранных Space/project/environment; >100 => query error.'
byid[19]['description']+=' Это фиксированный expected agent из alert config, не Jenkins agent filter.'
byid[1]['gridPos']['h']=5
byid[1]['options']['content']=f'### CI/CD HEALTH · {ENV}\n**Пайплайн:** ${{pipeline:text}} · **TC:** ${{tc_build_type}} · **Octopus:** ${{octopus_project}} / ${{octopus_environment}}\n\nФильтры меняют представление. Алерты и инфраструктурные пороги используют scopes из конфигурации.'
byid[5]['description']='Health всей цепочки: fixed infrastructure fleet плюс результаты выбранных TC buildType, Jenkins jobs и Octopus project/environment. Component filter не исключает систему из общего health.'
for pid in (2,3,4):
    byid[pid]['description']='Fixed infrastructure scope + последний результат выбранного pipeline. Error/NoData не подставляется как HEALTHY. UI filters не меняют alert scopes.'
    for link in byid[pid].get('links',[]):
        if link['title']=='Детали в Grafana':link.update(includeVars=True,keepTime=True)
# Stable IDs preserve existing details links and alert annotations.
layout=[('01 Общее состояние',[(2,0,6,5),(3,6,6,5),(4,12,6,5),(5,18,6,5)]),
        ('02 Результаты и длительность',[(6,0,8,4),(7,8,8,4),(8,16,8,4),
                                       (15,0,8,4),(16,8,8,4),(17,16,8,4),(25,0,24,6)]),
        ('03 Агенты и очереди',[(9,0,6,4),(10,6,6,4),(11,12,6,4),(12,18,6,4),(13,0,24,6)]),
        ('04 Текущие сборки и деплои',[(18,0,8,5),(22,8,8,5),(21,16,8,5)]),
        ('05 Доступность и время ответа',[(14,0,12,7),(24,12,12,7)]),
        ('06 Детали компонентов',[(19,0,8,8),(20,8,8,8),(23,16,8,8)]),
        ('07 Активные алерты',[(26,0,24,7)])]
arranged=[byid[1]];y=5
for title,members in layout:
    arranged.append({'id':next(ids),'type':'row','title':title,'collapsed':False,
                     'gridPos':{'x':0,'y':y,'w':24,'h':1},'panels':[]})
    y+=1;xprev=-1;lineheight=0
    for pid,x,w,h in members:
        if x<=xprev:y+=lineheight;lineheight=0
        pp=byid[pid];pp['gridPos']={'x':x,'y':y,'w':w,'h':h}
        arranged.append(pp);lineheight=max(lineheight,h);xprev=x
    y+=lineheight
assert len({p['id'] for p in arranged})==len(arranged)
d={'id':None,'uid':'cicd-health','title':'CI/CD Health · Native and REST','schemaVersion':39,'version':4,
   'editable':True,'style':'dark','tags':['cicd','opensource'],'timezone':'browser',
   'time':{'from':'now-6h','to':'now'},'refresh':'30s','panels':arranged,
   'templating':{'list':variables},'annotations':{'list':[]},
   'description':'Dashboard scope filters; fixed server environment and fixed alert scopes. REST errors never imply healthy.'}
(OUT/'cicd-health-dashboard.json').write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')

rules=[]
def alert(uid,title,query,condition='$A > 0',hold='0s',severity='warning',summary='',nodata='Alerting'):
    q=copy.deepcopy(query);q['refId']='A';q.pop('hide',None)
    ds=q['datasource']['uid']
    rules.append({'uid':uid,'title':'CI/CD · '+title,'condition':'B','for':hold,'noDataState':nodata,'execErrState':'Alerting','isPaused':False,'labels':{'domain':'cicd','severity':severity,'env':ENV,'team':'platform'},'annotations':{'summary':summary,'__dashboardUid__':'cicd-health','__panelId__':'5'},'data':[
      {'refId':'A','datasourceUid':ds,'relativeTimeRange':{'from':600,'to':0},'model':q},
      {'refId':'B','datasourceUid':'__expr__','relativeTimeRange':{'from':0,'to':0},'model':math(condition)}]})
alert('oss-service-down','Server HTTP or scrape unavailable',pquery(f'ci:observed_down{{{E}}}'),hold='2m',severity='critical',summary='HTTP/readiness or authenticated native scrape failed.')
alert('oss-observation-missing','Missing observations',pquery(f'ci:observation_missing{{{E}}}'),hold='3m',summary='Expected probe/native KPI missing; do not treat missing as success.')
alert('oss-tc-failed','TeamCity last build failed',inf('tc_failed'),summary='Latest selected terminal build is failure. Polling may miss fail→success between polls.')
alert('oss-jenkins-failed','Jenkins last build failed',pquery(f'default_jenkins_builds_last_build_result_ordinal{{{JOBS}}} == bool 2'),summary='Latest observed job result FAILURE; unstable/aborted distinct.')
alert('oss-deploy-failed','Octopus last deploy failed',inf('oc_failed'),severity='critical',summary='Latest-created terminal Deploy task failed/timed out in configured project/environment.')
alert('oss-octopus-api','Octopus API unavailable',inf('oc_api'),condition='$A < 1',hold='2m',severity='critical',summary='Live REST query must return a valid tasks response. HTTP/auth/schema/query errors alert, even with dashboard closed.')
alert('oss-tc-agent','TeamCity expected agent unavailable',inf('tc_agent'),condition='$A < 1',hold='5m',summary='Expected stable ID missing, disconnected, unauthorized or disabled.')
alert('oss-jenkins-agent','Jenkins expected node unavailable',pquery(f'(default_jenkins_nodes_online{{{J},node='+json.dumps(C['jenkins_expected_node'])+'} == bool 0) or vector(0)'),hold='5m',summary='Expected stable node offline. Missing node is separately detected by inventory alert.')
alert('oss-jenkins-node-missing','Jenkins expected node metric missing',pquery(f'absent(default_jenkins_nodes_online{{{J},node='+json.dumps(C['jenkins_expected_node'])+'})'),hold='5m',summary='Expected stable node disappeared from plugin metrics.',nodata='OK')
for c in ('teamcity','jenkins'):
    alert('oss-'+c+'-agents-low',c+' insufficient agents',pquery(f'ci:agents_online{{{E},component="{c}"}}'),condition=f'$A < {C[c+"_min_agents"]}',hold='5m',summary='Online/connected agent count below configured minimum; not a compatibility guarantee.')
    alert('oss-'+c+'-queue',c+' queue over limit',pquery(f'ci:queue_length{{{E},component="{c}"}}'),condition=f'$A > {C[c+"_queue_limit"]}',hold='10m')
alert('oss-octopus-queue','Octopus queue over limit',inf('oc_queue'),condition=f'$A > {C["octopus_queue_limit"]}',hold='10m')
alert('oss-tc-long','TeamCity build too long',inf('tc_running'),condition=f'$A > {C["teamcity_timeout_seconds"]}',hold='5m')
alert('oss-jenkins-long','Jenkins build too long',pquery(f'max(default_jenkins_builds_running_build_duration_milliseconds{{{JOBS}}})/1000'),condition=f'$A > {C["jenkins_timeout_seconds"]}',hold='5m',nodata='OK',summary='Plugin may omit runtime when no builds are running; verify metric contract on installed version.')
alert('oss-http-slow','HTTP response too slow',pquery(f'probe_duration_seconds{{job="cicd-http",{E}}}'),condition='$A > 2',hold='5m')
alert('oss-rules-missing','Recording rules missing',pquery(' or '.join(f'absent(ci:expected_component{{{E},component="{c}"}})' for c in ('teamcity','jenkins','octopus'))),hold='2m',severity='critical',nodata='OK')
old=['cicd-server-down','cicd-data-stale','cicd-build-failed','cicd-deploy-failed','cicd-agent-down','cicd-agents-low','cicd-capacity','cicd-queue-size','cicd-queue-age','cicd-long-build','cicd-slow-http','cicd-native-scrape','cicd-rules-missing']
if not DASHBOARD_ONLY:(OUT/'grafana-alert-rules.yml').write_text(yaml.safe_dump({'apiVersion':1,'deleteRules':[{'orgId':1,'uid':u} for u in old],'groups':[{'orgId':1,'name':'CI-CD Native REST','folder':'CI-CD Operations','interval':'60s','rules':rules}]},sort_keys=False,allow_unicode=True))
print(f'{len(panels)} data panels, {len(layout)} rows, {len(variables)} variables, {len(queries)} live REST queries, {len(rules)} alert rules')
