#!/usr/bin/env python3
"""Generate static Grafana provisioning, not an exporter or deployed service."""
import copy
import json
import pathlib
import sys
import urllib.parse
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG = json.loads(pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / 'config.example.json').read_text())
COMPONENTS = ('teamcity', 'jenkins', 'octopus')
INF = 'yesoreyeram-infinity-datasource'
PROM = {'type': 'prometheus', 'uid': 'cicd-prometheus'}
LIMIT = int(CFG['recent_limit'])
INV = int(CFG['inventory_limit'])
WINDOW = int(CFG['failure_window_seconds'])
SPACE = urllib.parse.quote(CFG['octopus_space_id'], safe='')
# Parse timestamps with fractional seconds and explicit offsets, including TC compact dates.
JQ = r'''
def arr(k): if (.[k]|type)=="array" then .[k] else error("Expected array: " + k) end;
def epoch:
  if . == null then null else
  capture("^(?<y>[0-9]{4})-?(?<m>[0-9]{2})-?(?<d>[0-9]{2})T(?<h>[0-9]{2}):?(?<mi>[0-9]{2}):?(?<s>[0-9]{2})(?:\\.[0-9]+)?(?<z>Z|[+-][0-9]{2}:?[0-9]{2})$") |
  . as $t | (($t.y+"-"+$t.m+"-"+$t.d+"T"+$t.h+":"+$t.mi+":"+$t.s+"Z") | fromdateiso8601) -
  (if $t.z=="Z" then 0 else (($t.z[1:3]|tonumber)*3600 + ($t.z|gsub(":";"")|.[3:5]|tonumber)*60) * (if $t.z[0:1]=="+" then 1 else -1 end) end)
  end;
def point(v): {time:(now*1000|floor),value:v};
def complete_inventory(k): if (.nextHref // "") != "" or ((.TotalResults // 0) > (.[k]|length)) then error("Inventory truncated: increase inventory_limit") else arr(k) end;
def mean: if length==0 then null else add/length end;
'''

def ds(c):
    return {'type': INF, 'uid': 'cicd-' + c}


def api(c, path, selector, params=None, columns=None, ref='A'):
    return {'refId': ref, 'datasource': ds(c), 'type': 'json', 'source': 'url', 'url': path,
            'url_options': {'method': 'GET', 'params': [{'key': k, 'value': str(v)} for k, v in (params or {}).items()]},
            'parser': 'jq-backend', 'format': 'timeseries' if columns is None else 'table',
            'root_selector': JQ + selector, 'columns': columns or [
                {'selector': 'time', 'text': 'Time', 'type': 'timestamp_epoch'},
                {'selector': 'value', 'text': 'Value', 'type': 'number'}]}


def prom(expr, ref='A', instant=True):
    return {'refId': ref, 'datasource': PROM, 'expr': expr, 'instant': instant, 'range': not instant, 'legendFormat': '{{component}} {{pod}} {{container}}'}


def expr(ref, expression, kind='math'):
    model = {'refId': ref, 'datasource': {'type': '__expr__', 'uid': '__expr__'}, 'type': kind, 'expression': expression}
    if kind == 'reduce':
        model.update(reducer='last', settings={'mode': 'strict'})
    return model


def probe(c):
    return 'min(probe_success{job="cicd-blackbox",component="' + c + '"}) or vector(0)'


def tc_build(state):
    count = LIMIT if state == 'finished' else INV
    return {'locator': f'state:{state},branch:default:any,count:{count}',
            'fields': 'nextHref,build(id,buildTypeId,number,status,state,startDate,finishDate,webUrl,canceledInfo,agent(id))'}

builds = 'fullName,url,builds[number,result,building,timestamp,duration,url]{0,' + str(LIMIT) + '}'
node_tree = builds + ',jobs[name,_class]'
for _ in range(int(CFG['jenkins_folder_depth'])):
    node_tree = builds + ',jobs[' + node_tree + ']'
JENKINS_PARAMS = {'tree': 'jobs[' + node_tree + ']'}
# Recursive traversal of the returned Jenkins folders, reject undisclosed/deeper inventories.
JENKINS_ROWS = r'''arr("jobs") | {jobs:.} | [. | recurse(.jobs[]?) | select(has("fullName") or has("_class")) | select(has("jobs")|not) | if (has("builds")|not) then error("Jenkins leaf data incomplete or folder depth exceeded") else . end | . as $j | .builds[] | {id:(.number|tostring),workflow:$j.fullName,status:(if .building then "RUNNING" else .result end),start:(.timestamp/1000),finish:((.timestamp+.duration)/1000),duration:(.duration/1000),url:.url}]'''
TC_ROWS = '[arr("build")[] | {id:(.id|tostring),workflow:.buildTypeId,status:(if .canceledInfo != null then "CANCELED" else .status end),start:(.startDate|epoch),finish:(.finishDate|epoch),duration:(if .startDate!=null and .finishDate!=null then (.finishDate|epoch)-(.startDate|epoch) else null end),url:.webUrl}]'
OCTO_ROWS = '[arr("Items")[] | {id:.Id,workflow:(.ProjectId // .Name),status:.State,start:(.StartTime|epoch),finish:(.CompletedTime|epoch),duration:(if .StartTime!=null and .CompletedTime!=null then (.CompletedTime|epoch)-(.StartTime|epoch) else null end),url:(.Links.Web // "")}]'


def build_query(c, suffix, state='finished'):
    if c == 'teamcity':
        rows = TC_ROWS if state == 'finished' else 'complete_inventory("build") | {build:.} | ' + TC_ROWS
        return api(c, '/app/rest/builds', rows + ' | ' + suffix, tc_build(state))
    if c == 'jenkins':
        rows = JENKINS_ROWS + ' | map(select(.status ' + ('!=' if state == 'finished' else '==') + ' "RUNNING"))'
        if state == 'finished':
            rows += f' | sort_by(.finish) | reverse | .[:{LIMIT}]'
        return api(c, '/api/json', rows + ' | ' + suffix, JENKINS_PARAMS)
    states = 'Success,Failed,Canceled,TimedOut' if state == 'finished' else 'Executing,Cancelling'
    rows = OCTO_ROWS if state == 'finished' else 'complete_inventory("Items") | {Items:.} | ' + OCTO_ROWS
    return api(c, f'/api/{SPACE}/tasks', rows + ' | ' + suffix, {'name': 'Deploy', 'states': states, 'take': LIMIT if state == 'finished' else INV})

queries = {}
for c in COMPONENTS:
    success = '.status=="Success"' if c == 'octopus' else '.status=="SUCCESS"'
    failure = '(.status=="Failed" or .status=="TimedOut")' if c == 'octopus' else '(.status=="FAILURE" or .status=="UNSTABLE")'
    eligibility = f'({success} or {failure})'
    queries[c] = {
        'success': build_query(c, f'map(select({eligibility})) | if length==0 then empty else point((map(select({success}))|length)/length*100) end'),
        'sample': build_query(c, f'point(map(select({eligibility}))|length)'),
        'duration': build_query(c, 'map(.duration | select(.!=null)) | if length==0 then empty else point(mean) end'),
        'failure': build_query(c, f'point([.[] | select({failure}) | select(.finish!=null and .finish>=(now-{WINDOW}) and .finish<=now)]|length)'),
        'age': build_query(c, 'point(([.[] | .start | select(.!=null) | now-.] | max // 0))', 'running'),
        'history': build_query(c, '.[]'),
        'running': build_query(c, '.[] | {workflow,status,age:(if .start==null then null else now-.start end),url}', 'running')
    }
    if c == 'teamcity':
        path, params = '/app/rest/agents', {'locator': f'defaultFilter:false,count:{INV}', 'fields': 'nextHref,agent(id,name,connected,enabled,authorized)'}
        rows = 'complete_inventory("agent")'
        available = '[.[] | select(.connected==true and .enabled==true and .authorized==true)] | length'
        offline = '[.[] | select(.connected==false and .enabled==true and .authorized==true)] | length'
        queries[c]['queue'] = api(c, '/app/rest/buildQueue', 'point(complete_inventory("build")|length)', {'locator': f'count:{INV}', 'fields': 'nextHref,build(id)'})
        details = '.[] | {agent:.name,online:.connected,enabled:.enabled,authorized:.authorized}'
    elif c == 'jenkins':
        path, params = '/computer/api/json', {'tree': 'computer[displayName,offline,temporarilyOffline,numExecutors,idle]'}
        rows = 'arr("computer") | map(select(.numExecutors>0))'
        available = '[.[] | select(.offline==false and .temporarilyOffline==false)] | length'
        offline = '[.[] | select(.offline==true and .temporarilyOffline==false)] | length'
        queries[c]['queue'] = api(c, '/queue/api/json', 'point(arr("items")|length)', {'tree': 'items[id]'})
        details = '.[] | {agent:.displayName,online:(.offline|not),enabled:(.temporarilyOffline|not),executors:.numExecutors,idle:.idle}'
        queries[c]['idle'] = api(c, path, rows + ' | point([.[] | select(.offline==false and .temporarilyOffline==false and .idle==true)]|length)', params)
    else:
        path, params = f'/api/{SPACE}/workers', {'take': INV}
        rows = 'complete_inventory("Items")'
        available = '[.[] | select(.HealthStatus=="Healthy" and .IsDisabled==false)] | length'
        offline = '[.[] | select(.HealthStatus!="Healthy" and .IsDisabled==false)] | length'
        queries[c]['queue'] = api(c, f'/api/{SPACE}/tasks', 'if (.TotalResults|type)!="number" then error("Expected TotalResults") else point(.TotalResults) end', {'name': 'Deploy', 'states': 'New,Queued', 'take': 1})
        details = '.[] | {agent:.Name,health:.HealthStatus,enabled:(.IsDisabled|not),workerPools:(.WorkerPoolIds|join(", "))}'
    queries[c]['agents'] = api(c, path, rows + ' | point(' + available + ')', params)
    queries[c]['offline'] = api(c, path, rows + ' | point(' + offline + ')', params)
    queries[c]['agent_details'] = api(c, path, rows + ' | ' + details, params)
    queries[c]['api_check'] = api(c, path, rows + ' | point(1)', params)


def health_targets(c):
    targets = [prom(probe(c), 'A')]
    for ref, key in [('B','queue'), ('C','agents'), ('D','failure'), ('E','age'), ('F','offline')]:
        q = copy.deepcopy(queries[c][key]); q['refId'] = ref
        targets.append(q)
    for ref in 'ABCDEF':
        targets.append(expr('R' + ref, ref, 'reduce'))
    timeout = CFG['deploy_timeout_seconds'] if c=='octopus' else CFG['build_timeout_seconds']
    targets.append(expr('H', f'2*$RA - $RA*(($RB>{CFG["queue_warning"]}) || ($RC<{CFG["min_agents"][c]}) || ($RD>0) || ($RE>{timeout}) || ($RF>0))'))
    return targets

panels = []
next_id = 1


def panel(title, kind, x, y, w, h, targets=None, unit='short', description='', links=None):
    global next_id
    p = {'id': next_id, 'title': title, 'type': kind, 'gridPos': {'x':x,'y':y,'w':w,'h':h}, 'description': description,
         'fieldConfig': {'defaults': {'unit':unit,'noValue':'Нет данных','color':{'mode':'thresholds'}, 'thresholds': {'mode':'absolute','steps':[{'color':'green','value':None}]}},'overrides':[]},
         'options': {'reduceOptions':{'calcs':['lastNotNull'],'fields':'','values':False}, 'orientation':'auto','textMode':'auto','colorMode':'value','graphMode':'none','justifyMode':'auto'}, 'targets': targets or []}
    if links:
        p['links'] = links
    if kind=='state-timeline':
        p['options']={'mergeValues':True,'showValue':'never','alignValue':'left','rowHeight':0.8,'legend':{'showLegend':True,'displayMode':'list','placement':'bottom'},'tooltip':{'mode':'single'}}
        p['fieldConfig']['defaults']['mappings']=[{'type':'value','options':{'0':{'text':'DOWN','color':'red'},'1':{'text':'UP','color':'green'}}}]
        p['fieldConfig']['defaults']['thresholds']['steps']=[{'color':'red','value':None},{'color':'green','value':1}]
    if kind=='timeseries':
        p['fieldConfig']['defaults']['color']={'mode':'palette-classic'}
        p['fieldConfig']['defaults']['custom']={'drawStyle':'line','lineInterpolation':'smooth','lineWidth':2,'fillOpacity':12,'showPoints':'never','spanNulls':False}
        p['options'] = {'legend':{'displayMode':'list','placement':'bottom'},'tooltip':{'mode':'multi','sort':'desc'}}
    if kind=='gauge':
        p['options']={'reduceOptions':{'calcs':['lastNotNull'],'fields':'','values':False},'orientation':'auto','showThresholdLabels':False,'showThresholdMarkers':True}
        p['fieldConfig']['defaults'].update(min=0,max=100,decimals=1)
    if kind=='table':
        p['options'] = {'showHeader':True,'cellHeight':'sm','footer':{'show':False}}
    panels.append(p); next_id += 1
    return p


def row(title, y):
    p = panel(title,'row',0,y,24,1); p['collapsed']=False; p['panels']=[]; p.pop('targets')


def health_panel(title, x, y, w, targets, link=None):
    p = panel(title, 'stat', x,y,w,5,targets,description='0: HTTP недоступен; 1: деградация (ошибки за 10 минут, очередь, агенты, длительность); 2: здоров. Ошибка API: нет данных. KPI — текущий snapshot.')
    p['options']['reduceOptions']['fields']='/^H$/'
    p['options']['colorMode']='background'
    p['fieldConfig']['defaults']['mappings']=[{'type':'value','options':{'0':{'text':'НЕДОСТУПЕН','color':'red'},'1':{'text':'ДЕГРАДАЦИЯ','color':'orange'},'2':{'text':'ЗДОРОВ','color':'green'}}}]
    p['fieldConfig']['defaults']['thresholds']['steps']=[{'color':'red','value':None},{'color':'orange','value':1},{'color':'green','value':2}]
    if link: p['links']=[{'title':'Открыть подробности','url':link,'targetBlank':False}]

row('CI/CD • Operational Health',0)
# Overall health is computed from the same HTTP/API criteria as the individual cards.
overall = []
for i,c in enumerate(COMPONENTS):
    for t in health_targets(c):
        t = copy.deepcopy(t)
        old = t['refId']; t['refId']=str(i)+old
        if t.get('datasource',{}).get('uid')=='__expr__':
            if t['type']=='reduce': t['expression']=str(i)+t['expression']
            else:
                for ref in 'ABCDEF': t['expression']=t['expression'].replace('$R'+ref, '${'+str(i)+'R'+ref+'}')
        overall.append(t)
overall.append(expr('H','(${0H}==0 || ${1H}==0 || ${2H}==0)*0 + (${0H}>0 && ${1H}>0 && ${2H}>0)*(1+(${0H}==2 && ${1H}==2 && ${2H}==2))'))
health_panel('Вся CI/CD цепочка',0,1,6,overall)
for i,c in enumerate(COMPONENTS):
    health_panel(c.capitalize(),6+i*6,1,6,health_targets(c),'/d/cicd-'+c+'-details')
p=panel('Требуют внимания • Grafana Alerting', 'alertlist',0,6,24,7)
p['options']={'viewMode':'list','groupMode':'default','maxItems':20,'sortOrder':1,'stateFilter':{'firing':True,'pending':True,'error':True,'noData':True,'normal':False},'alertName':'CI/CD','dashboardAlerts':False}
row('KPI • последние результаты и текущая загрузка',13)
for i,c in enumerate(COMPONENTS):
    x=i*8
    for k,key,title,unit in [(0,'success','Успешность последних N','percent'),(1,'agents','Доступные агенты / workers','short'),(2,'queue','Очередь','short')]:
        p=panel(c.capitalize()+' • '+title,'gauge' if key=='success' else 'stat',x+[0,3,6][k],14, 3 if k<2 else 2,4,[queries[c][key]],unit,
                'Последние N завершённых сборок/Deploy tasks. N='+str(LIMIT)+'. Canceled исключены из успешности; Jenkins UNSTABLE считается неуспехом. Workers Octopus не равны deployment targets; built-in worker здесь не учитывается.')
        if key=='success': p['fieldConfig']['defaults']['thresholds']['steps']=[{'color':'red','value':None},{'color':'orange','value':90},{'color':'green','value':99}]
        if key=='queue': p['fieldConfig']['defaults']['thresholds']['steps']=[{'color':'green','value':None},{'color':'orange','value':CFG['queue_warning']+1}]
    for k,key,title in [(0,'duration','Средняя длительность • последние N'),(1,'age','Самая долгая текущая сборка / деплой')]:
        panel(c.capitalize()+' • '+title,'stat',x+k*4,18,4,4,[queries[c][key]],'s')
row('Доступность и время отклика • Prometheus / Blackbox',22)
for x,title,query,unit in [(0,'HTTP availability', 'probe_success{job="cicd-blackbox"}', 'short'),(8,'HTTP response time','probe_duration_seconds{job="cicd-blackbox"}','s'),(16,'Availability за выбранный период','100*avg_over_time(probe_success{job="cicd-blackbox"}[$__range])','percent')]:
    panel(title,'state-timeline' if x==0 else 'timeseries' if x==8 else 'gauge',x,23,8,7,[prom(query,instant=x==16)],unit, 'Проверяются HTTP-код 200 и TLS, редиректы отключены. Это проверка сервера, не статуса pod.')
row('Kubernetes • состояние инфраструктуры',30)
ns=CFG['namespace_regex']; pods=CFG['pod_regex']
sel='namespace=~"$namespace",pod=~"$pod"'
for x,title,query,unit in [(0,'Pod Ready',f'min by(namespace,pod) (kube_pod_status_ready{{{sel},condition="true"}})','short'),(8,'CPU • cores',f'sum by(namespace,pod) (rate(container_cpu_usage_seconds_total{{{sel},container!="",container!="POD"}}[$__rate_interval]))','cores'),(16,'Memory • working set',f'sum by(namespace,pod) (container_memory_working_set_bytes{{{sel},container!="",container!="POD"}})','bytes')]:
    panel(title,'timeseries',x,31,8,7,[prom(query,instant=False)],unit)
panel('Рестарты контейнеров', 'timeseries',0,38,12,6,[prom(f'sum by(namespace,pod) (increase(kube_pod_container_status_restarts_total{{{sel}}}[$__rate_interval]))',instant=False)])
panel('Состояние сбора • /app/metrics и /prometheus/', 'timeseries',12,38,12,6,[prom('up{job=~"cicd-teamcity|cicd-jenkins"}',instant=False)])
p=panel('Границы данных', 'text',0,44,24,4)
p['options']={'mode':'markdown','content':f'**Infinity = snapshot API; Prometheus = временные ряды.** Последние {LIMIT} завершённых результатов не зависят от time picker. История Kubernetes и HTTP зависит от периода. Алерты опрашивают API каждую минуту без открытого dashboard. Общий health включает HTTP, очередь, доступность агентов, ошибки за последние {WINDOW//60} минут и текущие долгие выполнения. При ошибке/неполном ответе API показывается «Нет данных». Клики на карточках открывают подробности. Octopus: workers, только tasks типа Deploy; targets показаны отдельно.'}


def dashboard(uid, title, panels):
    return {'id':None,'uid':uid,'title':title,'tags':['cicd','operational'],'timezone':'browser','schemaVersion':39,'version':1,'editable':True,'refresh':'1m','time':{'from':'now-6h','to':'now'},'panels':panels,'annotations':{'list':[{'builtIn':1,'datasource':{'type':'grafana','uid':'-- Grafana --'},'enable':True,'hide':True,'iconColor':'rgba(0, 211, 255, 1)','name':'Annotations & Alerts','type':'dashboard'}]},'templating':{'list':[{'name':'namespace','label':'Kubernetes namespace regex','type':'textbox','query':ns,'current':{'text':ns,'value':ns}}, {'name':'pod','label':'Pod regex','type':'textbox','query':pods,'current':{'text':pods,'value':pods}}]},'links':[{'title':'CI/CD Health','url':'/d/cicd-operational-health','type':'link'},{'title':'TeamCity','url':'/d/cicd-teamcity-details','type':'link'},{'title':'Jenkins','url':'/d/cicd-jenkins-details','type':'link'},{'title':'Octopus','url':'/d/cicd-octopus-details','type':'link'}]}


def save_json(path, data):
    (ROOT/path).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')

save_json('dashboards/cicd-health.json',dashboard('cicd-operational-health','CI/CD • Operational Health',panels))
for c in COMPONENTS:
    panels=[];next_id=1
    row(c.capitalize()+' • детали',0)
    for i,key in enumerate(['success','sample','agents','offline','queue','age']):
        panel({'success':'Успешность • последние N','sample':'Завершённые SUCCESS/FAILURE в выборке','agents':'Доступные агенты / workers','offline':'Неожиданно недоступные агенты / workers','queue':'Очередь','age':'Самое долгое текущее выполнение'}[key],'stat',i*4,1,4,4,[queries[c][key]],'percent' if key=='success' else 's' if key=='age' else 'short')
    cols=[{'selector':name,'text':title,'type':typ} for name,title,typ in [('workflow','Workflow','string'),('id','ID','string'),('status','Status','string'),('duration','Duration','number'),('start','Start','timestamp_epoch_s'),('finish','Finish','timestamp_epoch_s'),('url','Open','string')]]
    q=copy.deepcopy(queries[c]['history']);q.update(format='table',columns=cols)
    p=panel('Последние '+str(LIMIT)+' завершённых результатов','table',0,5,24,12,[q])
    p['fieldConfig']['overrides']=[{'matcher':{'id':'byName','options':'Status'},'properties':[{'id':'mappings','value':[{'type':'value','options':{'SUCCESS':{'text':'SUCCESS','color':'green'},'Success':{'text':'SUCCESS','color':'green'},'FAILURE':{'text':'FAILED','color':'red'},'Failed':{'text':'FAILED','color':'red'},'TimedOut':{'text':'TIMEOUT','color':'red'},'UNSTABLE':{'text':'UNSTABLE','color':'orange'},'CANCELED':{'text':'CANCELED','color':'gray'},'Canceled':{'text':'CANCELED','color':'gray'},'ABORTED':{'text':'ABORTED','color':'gray'}}}]},{'id':'custom.cellOptions','value':{'type':'color-background'}}]},{'matcher':{'id':'byName','options':'Open'},'properties':[{'id':'links','value':[{'title':'Открыть результат','url':'${__value.raw}','targetBlank':True}]}]},{'matcher':{'id':'byName','options':'Duration'},'properties':[{'id':'unit','value':'s'}]}]
    q=copy.deepcopy(queries[c]['running']);q.update(format='table',columns=[{'selector':k,'text':k,'type':t} for k,t in [('workflow','string'),('status','string'),('age','number'),('url','string')]])
    panel('Текущие выполнения','table',0,17,12,10,[q],'short','Нет выполняющихся задач — пустая таблица, а не ошибка.')
    q=copy.deepcopy(queries[c]['agent_details']);q['format']='table'
    cols={'teamcity':[('agent','string'),('online','boolean'),('enabled','boolean'),('authorized','boolean')], 'jenkins':[('agent','string'),('online','boolean'),('enabled','boolean'),('executors','number'),('idle','boolean')], 'octopus':[('agent','string'),('health','string'),('enabled','boolean'),('workerPools','string')]}[c]
    q['columns']=[{'selector':k,'text':k,'type':t} for k,t in cols]
    panel('Агенты / workers • инвентарь','table',12,17,12,10,[q])
    panel('HTTP response time','timeseries',0,27,12,7,[prom(f'probe_duration_seconds{{job="cicd-blackbox",component="{c}"}}',instant=False)],'s')
    panel('HTTP availability','timeseries',12,27,12,7,[prom(f'probe_success{{job="cicd-blackbox",component="{c}"}}',instant=False)])
    if c=='octopus':
        q=api(c,f'/api/{SPACE}/machines','complete_inventory("Items")[] | {name:.Name,health:.HealthStatus,disabled:.IsDisabled}',{'take':INV},[{'selector':k,'text':k,'type':t} for k,t in [('name','string'),('health','string'),('disabled','boolean')]])
        panel('Deployment targets • отдельно от workers','table',0,34,24,10,[q])
    if c=='jenkins':
        prefix=CFG.get('jenkins_metric_prefix','default_jenkins')
        native=[('Executor capacity',f'{prefix}_executors_available{{job="cicd-jenkins"}}','short'),('Executor queue',f'{prefix}_executors_queue_length{{job="cicd-jenkins"}}','short'),('Build duration',f'{prefix}_builds_last_build_duration_milliseconds{{job="cicd-jenkins"}} / 1000','s'),('Running build duration',f'{prefix}_builds_running_build_duration_milliseconds{{job="cicd-jenkins"}} / 1000','s')]
        for i,(title,query,unit) in enumerate(native):
            q=prom(query,instant=False);q['legendFormat']='{{job}} {{jenkins_job}} {{node}}'
            panel(title+' • Prometheus Plugin','timeseries',(i%2)*12,34+(i//2)*7,12,7,[q],unit,'Нативная метрика Jenkins Prometheus Plugin. Namespace/prefix настраивается в config.json; отсутствие метрики остаётся No data.')
    if c=='teamcity':
        for i,(title,metric_name) in enumerate([('Running builds','builds_running_number'),('Queue','builds_queued_number'),('Connected authorized agents','agents_connected_authorized_number')]):
            panel(title+' • /app/metrics','timeseries',i*8,34,8,7,[prom('max('+metric_name+'{job="cicd-teamcity"})',instant=False)],description='Нативные метрики, используемые в публичном TeamCity dashboard 11669. Проверьте имена в вашей версии /app/metrics; этот ряд не участвует в общем health.')
    d=dashboard('cicd-'+c+'-details','CI/CD • '+c.capitalize()+' Details',panels)
    d['links'].append({'title':'Открыть '+c.capitalize(),'url':CFG['public_urls'][c],'type':'link','targetBlank':True})
    save_json('dashboards/'+c+'-details.json',d)

# Grafana managed alerts use fixed datasource UIDs and no dashboard variables.
rules=[]

def alert(c,key,title,target,op,threshold,hold='0s',severity='warning'):
    a=copy.deepcopy(target);a['refId']='A'
    b=expr('B','A','reduce')
    cond=expr('C','B','threshold');cond['conditions']=[{'type':'query','evaluator':{'type':op,'params':[threshold]},'operator':{'type':'and'},'query':{'params':['C']},'reducer':{'type':'last','params':[]}}]
    def data(model):
        return {'refId':model['refId'],'relativeTimeRange':{'from':300,'to':0},'datasourceUid':model['datasource']['uid'],'model':model}
    rules.append({'uid':f'cicd-{c}-{key}','title':f'CI/CD {c}: {title}','condition':'C','data':[data(a),data(b),data(cond)],'for':hold,'noDataState':'Alerting','execErrState':'Alerting','labels':{'team':'cicd','component':c,'severity':severity},'annotations':{'summary':title,'description':'Проверьте подробный dashboard и Query Inspector. Ошибка/NoData источника также требует внимания; это не подтверждённый сбой сборки.','__dashboardUid__':'cicd-'+c+'-details','__panelId__':'2'},'isPaused':False})

for c in COMPONENTS:
    alert(c,'http-down','Сервер HTTP недоступен',prom(probe(c)),'lt',1,'2m','critical')
    alert(c,'api-check','REST API: ошибка сбора / неверные права / неполный инвентарь',queries[c]['api_check'],'lt',1,'2m','critical')
    alert(c,'failure','Неуспешный деплой за последние 10 минут' if c=='octopus' else 'Неуспешная сборка за последние 10 минут',queries[c]['failure'],'gt',0,'0s','critical')
    alert(c,'agents-offline','Неожиданно недоступный агент / worker',queries[c]['offline'],'gt',0,'5m')
    if CFG['min_agents'][c]>0:
        alert(c,'capacity','Доступных агентов / workers меньше минимума',queries[c]['agents'],'lt',CFG['min_agents'][c],'5m')
    alert(c,'queue','Очередь больше порога',queries[c]['queue'],'gt',CFG['queue_warning'],'5m')
    alert(c,'long-running','Слишком долгое выполнение',queries[c]['age'],'gt',CFG['deploy_timeout_seconds'] if c=='octopus' else CFG['build_timeout_seconds'],'5m')
    alert(c,'http-slow','HTTP response time больше 3 секунд',prom(f'max(probe_duration_seconds{{job="cicd-blackbox",component="{c}"}})'),'gt',3,'5m')
for c in ('teamcity','jenkins'):
    alert(c,'scrape-down','Prometheus scrape недоступен',prom(f'min(up{{job="cicd-{c}"}}) or vector(0)'),'lt',1,'3m')
alert('kubernetes','pods-not-ready','Pod не Ready более 5 минут',prom(f'min by(namespace,pod) (kube_pod_status_ready{{namespace=~"{ns}",pod=~"{pods}",condition="true"}})'),'lt',1,'5m')
# Grafana provisioning substitutes $ENV in strings; escape query/expression $ for literal delivery.
def escape(value):
    if isinstance(value,str): return value.replace('$','$$')
    if isinstance(value,list): return [escape(v) for v in value]
    if isinstance(value,dict): return {k:escape(v) for k,v in value.items()}
    return value
(ROOT/'provisioning/alerting/rules.yaml').write_text(yaml.safe_dump(escape({'apiVersion':1,'groups':[{'orgId':1,'name':'CI/CD operational','folder':'CI/CD','interval':'1m','rules':rules}]}),allow_unicode=True,sort_keys=False))
save_json('tests/query-catalog.json',queries)
print(f'Generated 4 dashboards, {len(rules)} Grafana alert rules, query catalog')
