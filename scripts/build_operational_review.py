"""Build Grafana v2 review revision from compact, credential-free source profile."""
import copy,json,re
from pathlib import Path
LABEL={'teamcity':'TeamCity','jenkins':'Jenkins','octopus':'Octopus'}
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'cicd-health'
P=json.loads((OUT/'review-source-profile.json').read_text())
NS='cicd-mia-899|octopus-worker-mia-899';PROM=P['prometheus_uid'];elements={};counter=100
COMMON=P['common'].replace('  capture(', '  (capture(').replace('$") |', '$") // error("Invalid timestamp")) |')+'''\ndef failed: .=="FAILURE" or .=="Failed" or .=="TimedOut";
def degraded: failed or .=="UNSTABLE";\ndef success: .=="SUCCESS" or .=="Success";\ndef ranked:\n  group_by(.workflow) | map(. as $g | ($g | map(select(.status|failed))|length) as $f |\n  ($g|sort_by(.start)|map(select((.status|failed) or (.status|success)))|map(.status|success)) as $s |\n  ([range(1;$s|length) as $i | select($s[$i]!=$s[$i-1])]|length) as $changes |\n  {workflow:$g[0].workflow,failures:$f,mean:($g|map(.duration|select(.!=null and .>0))|mean),max:($g|map(.duration|select(.!=null))|max),samples:($g|length),alternations:$changes,suspected_flaky:(($s|length)>=4 and ($changes/(($s|length)-1))>=0.5)}) | sort_by(-.failures,-(.mean//0));\n'''
# Keep the original datasource UIDs, context paths and automatic API variables.
def source(system,ref='D',root=None,table=False,columns=None):
    q=copy.deepcopy(P['sources'][system][ref]['query'])
    q['spec']['root_selector']=COMMON+(root if root is not None else P['sources'][system][ref]['root'])
    if system=='octopus' and ref in ('B','D','E'):
        q['spec']['root_selector']=COMMON+'if "${octopus_environment:raw}"=="__select_prod__" then error("Select a valid Prod environment") else . end | '+(root if root is not None else P['sources'][system][ref]['root'])
    if table:q['spec'].update(format='table',columns=columns or [])
    return q

def param(q,key,value):
    ps=q['spec'].setdefault('url_options',{}).setdefault('params',[])
    ps[:]=[p for p in ps if p['key']!=key];ps.append({'key':key,'value':value})
    return q

def extra(system,url,root,params=None,columns=None):
    q=source(system,root=root,table=True,columns=columns)
    q['spec']['url']=url;q['spec']['url_options']={'method':'GET','params':[{'key':k,'value':v} for k,v in (params or {}).items()]};return q

def col(selector,text=None,typ='string'):return {'selector':selector,'text':text or selector,'type':typ}
def cols(*names):return [col(n) for n in names]
def prom(expr,legend='',instant=False):return {'kind':'DataQuery','group':'prometheus','datasource':{'name':PROM},'version':'v0','spec':{'expr':expr,'instant':instant,'range':not instant,'format':'time_series','legendFormat':legend}}
def expr(kind,expression,ref):return {'kind':'DataQuery','group':'__expr__','datasource':{'name':'__expr__'},'version':'v0','spec':{'type':kind,'expression':expression,'refId':ref,**({'reducer':'last','settings':{'mode':'strict'}} if kind=='reduce' else {})}}
def queries(qs):return [{'kind':'PanelQuery','spec':{'refId':ref,'hidden':hide,'query':q}} for ref,q,hide in qs]
def override(field,props):return {'matcher':{'id':'byName','options':field},'properties':[{'id':k,'value':v} for k,v in props.items()]}
STATUS={'SUCCESS':{'text':'SUCCESS','color':'green'},'Success':{'text':'SUCCESS','color':'green'},'FAILURE':{'text':'FAILED','color':'red'},'Failed':{'text':'FAILED','color':'red'},'TimedOut':{'text':'TIMEOUT','color':'red'},'UNSTABLE':{'text':'UNSTABLE','color':'orange'},'ABORTED':{'text':'ABORTED','color':'gray'},'CANCELED':{'text':'CANCELED','color':'gray'},'Canceled':{'text':'CANCELED','color':'gray'}}

def panel(title,qs,typ='table',unit='short',desc='',thresholds=None,overrides=None,options=None,transforms=None,queryOptions=None,id=None):
    global counter
    if id is None:counter+=1;id=counter
    fc={'defaults':{'unit':unit,'noValue':'Нет данных','color':{'mode':'thresholds'},'thresholds':{'mode':'absolute','steps':thresholds or [{'color':'green','value':None}]},'mappings':[]},'overrides':overrides or []}
    op={'showHeader':True,'cellHeight':'sm'} if typ=='table' else {'reduceOptions':{'calcs':['lastNotNull'],'fields':'','values':False},'colorMode':'value','graphMode':'none','textMode':'auto'}
    if typ=='timeseries':
        op={'legend':{'displayMode':'table','placement':'bottom','calcs':['lastNotNull']},'tooltip':{'mode':'multi'}};fc['defaults'].update(color={'mode':'palette-classic'},custom={'lineWidth':2,'fillOpacity':8,'spanNulls':False,**({'thresholdsStyle':{'mode':'dashed'}} if thresholds else {})})
    if options:op.update(options)
    transforms=[{'kind':'Transformation','group':t['spec'].get('id',t['kind']),'spec':{'options':t['spec']['options']}} for t in (transforms or [])]
    key='panel-'+str(id)
    elements[key]={'kind':'Panel','spec':{'id':id,'title':title,'description':desc,'links':[],'data':{'kind':'QueryGroup','spec':{'queries':queries(qs),'transformations':transforms or [],'queryOptions':queryOptions or {}}},'vizConfig':{'kind':'VizConfig','group':typ,'version':'13.2.3','spec':{'fieldConfig':fc,'options':op}}}}
    return key

def layoutrow(title,members,collapse=False,nested=None):
    items=[];x=y=height=0
    for key,w,h in members:
        if x+w>24:y+=height;x=height=0
        items.append({'kind':'GridLayoutItem','spec':{'element':{'kind':'ElementReference','name':key},'x':x,'y':y,'width':w,'height':h}});x+=w;height=max(height,h)
    return {'kind':'RowsLayoutRow','spec':{'title':title,'collapse':collapse,'layout':nested or {'kind':'GridLayout','spec':{'items':items}}}}

def table_links():return [override('Результат',{'mappings':[{'type':'value','options':STATUS}],'custom.cellOptions':{'type':'color-background','applyToRow':True}}),override('Номер',{'links':[{'title':'Открыть запуск','targetBlank':True,'url':'${__data.fields["URL"]}'}]}),override('URL',{'custom.hidden':True}),override('Длительность',{'unit':'s'}),override('Начало',{'unit':'time:YYYY-MM-DD HH:mm:ss Z'}),override('Завершение',{'unit':'time:YYYY-MM-DD HH:mm:ss Z'})]

variables=copy.deepcopy(P['variables'])
# Source API defaults are retained, including All -> optional API filter macros.
variables=[v for v in variables if v['spec']['name'] not in ('namespace','pod')]
variables.append({'kind':'CustomVariable','spec':{'name':'namespace','label':'CI namespace','hide':'dontHide','multi':True,'includeAll':True,'allValue':NS,'query':'cicd-mia-899,octopus-worker-mia-899','current':{'text':['All'],'value':['$__all']},'options':[],'skipUrlSync':False}})
variables.append({'kind':'QueryVariable','spec':{'name':'pod','label':'CI pod','hide':'dontHide','multi':True,'includeAll':True,'allValue':'.*','current':{'text':['All'],'value':['$__all']},'options':[],'query':{'kind':'DataQuery','group':'prometheus','version':'v0','datasource':{'name':PROM},'spec':{'query':'label_values(kube_pod_info{namespace=~"'+NS+'",namespace=~"${namespace:regex}"}, pod)','refId':'variable'}},'refresh':'onDashboardLoad','sort':'alphabeticalAsc','skipUrlSync':False}})
ocvar=copy.deepcopy(next(v for v in variables if v['spec']['name']=='octopus_space'))
ocvar['spec'].update(name='octopus_environment',label='Octopus environment',multi=False,includeAll=False,current={'text':'Prod','value':'__select_prod__'})
q=ocvar['spec']['query']['spec']['infinityQuery'];q['url']='http://octopus.admortgage.com/api/${octopus_space}/environments';q['url_options']={'method':'GET','params':[{'key':'take','value':'1000'}]};q['root_selector']=COMMON+'complete_inventory("Items")[] | select(.Name|test("^(prod|production)$";"i")) | {__text:.Name,__value:.Id}'
# No guessed environment ID: a sentinel fails closed until Prod is selected.
variables.insert(1,ocvar)
for system in P['sources']:
    if system=='octopus':
        for ref in ('B','D','E'):param(P['sources'][system][ref]['query'],'environment','${octopus_environment:raw}')
# Broaden requested metadata; unsupported fields remain explicit null, never guessed.
for ref in ('D','E'):
    q=P['sources']['teamcity'][ref]['query'];ps=q['spec']['url_options']['params'];f=next(x for x in ps if x['key']=='fields');f['value']=f['value'].replace('agent(id)','agent(id,name),branchName,triggered(type,user(name),buildTypeId)')
q=P['sources']['teamcity']['B']['query'];next(x for x in q['spec']['url_options']['params'] if x['key']=='fields')['value']='nextHref,build(id,buildTypeId,queuedDate,waitReason,webUrl)'
for ref in ('A','C','F'):
    q=P['sources']['teamcity'][ref]['query'];next(x for x in q['spec']['url_options']['params'] if x['key']=='fields')['value']='nextHref,agent(id,name,connected,enabled,authorized,build(id),disconnectionComment)'
for ref in ('A','C','F'):
    q=P['sources']['jenkins'][ref]['query'];next(x for x in q['spec']['url_options']['params'] if x['key']=='tree')['value']='computer[displayName,offline,temporarilyOffline,offlineCauseReason,numExecutors,idle,busyExecutors,executors[idle,currentExecutable[url,number]]]'
for ref in ('D','E'):
    q=P['sources']['jenkins'][ref]['query'];t=next(x for x in q['spec']['url_options']['params'] if x['key']=='tree');t['value']=t['value'].replace('duration,url]', 'duration,url,builtOn,actions[causes[userName,shortDescription],parameters[name,value]]]')
q=P['sources']['jenkins']['B']['query'];next(x for x in q['spec']['url_options']['params'] if x['key']=='tree')['value']='items[id,inQueueSince,why,task[fullName,url]]'
# Normalize tables from original canonical queries, with extra columns.
N=copy.deepcopy(P['normalize'])
N['teamcity']=N['teamcity'].replace('url:.webUrl}', 'url:.webUrl,branch:.branchName,trigger:(.triggered.user.name//.triggered.type),agent:(.agent.name//(if .agent.id==null then null else (.agent.id|tostring) end))}')
N['jenkins']=N['jenkins'].replace('url:.url}', 'url:.url,branch:([.actions[]?.parameters[]?|select(.name=="BRANCH_NAME" or .name=="GIT_BRANCH" or .name=="BRANCH")|.value][0]),trigger:([.actions[]?.causes[]?|(.userName//.shortDescription)][0]),agent:.builtOn}')
N['octopus']=N['octopus'].replace('workflow:(.ProjectId // .Name)','workflow:(.ProjectId // .Arguments.ProjectId // (if "${octopus_project:raw}"=="*" then "MISSING_PROJECT" else "${octopus_project:raw}" end))').replace('url:((.Links.Web', 'branch:(.ReleaseVersion//.Arguments.ReleaseVersion//.Description),trigger:(.Arguments.DeployedBy//.Arguments.DeployedById//null),agent:(.ServerNodeId//null),url:((.Links.Web')
# Task resources do not reliably contain project/release/actor. Reuse one
# bounded deployment lookup, refreshed with the dashboard time range.
meta=copy.deepcopy(ocvar);meta['spec'].update(name='octopus_metadata',label='Deployment metadata',hide='hideVariable',current={'text':'metadata','value':'{}'},refresh='onTimeRangeChanged')
mq=meta['spec']['query']['spec']['infinityQuery'];mq['url']='http://octopus.admortgage.com/api/${octopus_space}/deployments$__combineValues(?projects=,,__comma,${octopus_project:csv})';mq['url_options']={'method':'GET','params':[{'key':'take','value':'1000'},{'key':'environments','value':'${octopus_environment:raw}'}]};mq['root_selector']=COMMON+'arr("Items") | map(select(.TaskId!=null)|{key:.TaskId,value:{project:.ProjectId,version:(.Changes.Version//.ReleaseId),trigger:.DeployedBy}}) | from_entries | {__text:"metadata",__value:tojson}'
variables.append(meta)
# Anchor the API query and filter sliding 24h in jq on every refresh.
clock=copy.deepcopy(meta);clock['spec'].update(name='prod_from24h',label='Prod 24h start',current={'text':'24h','value':'__refresh_24h__'})
cq=clock['spec']['query']['spec']['infinityQuery'];cq.update(source='inline',data='{}',root_selector='(now-86400|strftime("%Y-%m-%dT%H:%M:%SZ")) as $t | {__text:"24h",__value:$t}');cq.pop('url',None);cq.pop('url_options',None)
variables.append(clock)
names=copy.deepcopy(meta);names['spec'].update(name='octopus_project_names',label='Project names',current={'text':'projects','value':'{}'})
nq=names['spec']['query']['spec']['infinityQuery'];nq['url']='http://octopus.admortgage.com/api/${octopus_space}/projects';nq['url_options']={'method':'GET','params':[{'key':'take','value':'1000'}]};nq['root_selector']=COMMON+'complete_inventory("Items") | map({key:.Id,value:.Name}) | from_entries | {__text:"projects",__value:tojson}'
variables.append(names)
N['octopus']=N['octopus'].replace('[arr("Items")[] |', '(${octopus_metadata:json}|fromjson) as $meta | [arr("Items")[] | . as $task | .+{ProjectId:(.ProjectId//.Arguments.ProjectId//$meta[.Id].project),ReleaseVersion:(.ReleaseVersion//$meta[.Id].version),Arguments:((.Arguments//{})+{DeployedBy:(.Arguments.DeployedBy//$meta[.Id].trigger)})} |')
N['octopus']='(${octopus_project_names:json}|fromjson) as $names | '+N['octopus']+' | map(.workflow as $id | if $names[$id]!=null then .workflow=($names[$id]+" ["+$id+"]") else . end)'
# Pipeline runs often omit builtOn. Match their URL with computer executors.
jv=copy.deepcopy(next(v for v in variables if v['spec']['name']=='jenkins_pipeline'));jv['spec'].update(name='jenkins_agent_map',label='Executor map',hide='hideVariable',includeAll=False,multi=False,current={'text':'agents','value':'{}'},refresh='onTimeRangeChanged')
jq=jv['spec']['query']['spec']['infinityQuery'];jq['url']='http://jenkins.admortgage.com/computer/api/json';jq['url_options']={'method':'GET','params':[{'key':'tree','value':'computer[displayName,executors[currentExecutable[url]]]'}]};jq['root_selector']=COMMON+'arr("computer") | [.[] | .displayName as $agent | .executors[]? | select(.currentExecutable.url!=null) | {key:.currentExecutable.url,value:$agent}] | from_entries | {__text:"agents",__value:tojson}'
variables.append(jv)
N['jenkins']='(${jenkins_agent_map:json}|fromjson) as $agents | '+N['jenkins'].replace('agent:.builtOn','agent:(.builtOn//$agents[.url])')


def history(system,table=True,ref='D',tail='',columns=None):return source(system,ref,N[system]+tail,table,columns)

def results(system,failures=False):
    tail=' | map(select(.status|failed))' if failures else ''
    tail+=' | sort_by((if (.status|failed) then 0 else 1 end),-(.finish//0)) | .[]'
    tail+=' | {workflow,id,status,duration,start,finish,url,branch:(.branch//"Не передано API"),trigger:(.trigger//"Не передано API")}'
    cs=[col('workflow','Пайплайн'),col('id','Номер'),col('status','Результат'),col('branch','Ветка / релиз'),col('trigger','Кто запустил'),col('duration','Длительность','number'),col('start','Начало','timestamp_epoch_s'),col('finish','Завершение','timestamp_epoch_s'),col('url','URL')]
    return panel(LABEL[system]+(' — последние падения' if failures else ' — последние 100 результатов'),[('A',history(system,tail=tail,columns=cs),False)],desc='Текущая выборка последних 100. Падения первыми; отсутствующие metadata отмечены явно.',overrides=table_links())

status=[];current=[];deploy=[];problems=[];details=[];reasonkeys={}
for i,system in enumerate(('teamcity','jenkins','octopus')):
    qs=[]
    for ref in 'ABCDEF':qs.append((ref,source(system,ref),True))
    for ref in 'ABCDEF':qs.append(('R'+ref,expr('reduce',ref,'R'+ref),True))
    limit=3600 if system=='octopus' else 1800
    formula='2*$RA-$RA*(($RB>10)||($RD>0)||($RE>'+str(limit)+')||($RF>0)'+('||($RC<1)' if system!='octopus' else '')+')'
    qs.append(('H',expr('math',formula,'H'),False))
    key=panel(LABEL[system]+(' · Prod' if system=='octopus' else '')+' — текущее состояние',qs,'stat',desc='REST snapshot сейчас; KPI health учитывает очередь, агентов, последние ошибки и running age.',id=3+i,options={'reduceOptions':{'calcs':['lastNotNull'],'fields':'/^H$/','values':False},'colorMode':'background'})
    elements[key]['spec']['vizConfig']=copy.deepcopy(P['cards'][system]['vizConfig'])
    reasons=[]
    for ref in 'ABCDEF':
        zero='empty'
        text={'A':'"API отвечает"','B':'"queue " + (.value|tostring) + " > 10"','C':'"available agents " + (.value|tostring) + " < 1"','D':'"failures в выборке за 10 мин: " + (.value|tostring)','E':'"running age " + (.value|tostring) + "s > '+str(limit)+'s"','F':'(.value|tostring) + " agents offline"'}[ref]
        if system=='octopus' and ref=='F':text='(.value|tostring) + " workers health != Healthy"'
        if system=='jenkins' and ref=='D':text='"failure/unstable в выборке за 10 мин: " + (.value|tostring)'
        condition={'A':'.value==1','B':'.value>10','C':'.value<1','D':'.value>0','E':'.value>'+str(limit),'F':'.value>0'}[ref]
        if system=='octopus' and ref=='C':continue
        root=P['sources'][system][ref]['root']+' | if '+condition+' then {cause:('+text+')} else empty end'
        reasons.append((ref,source(system,ref,root,True,[col('cause','Причина')]),False))
    reason=panel(LABEL[system]+' — причины состояния',reasons,desc='Те же условия и пороги, что у плитки. Error/No data источника не означает отсутствие проблем.')
    reasonkeys[system]=reason
    link_vars='&'.join('${'+v+':queryparam}' for v in ('teamcity_pipeline','jenkins_pipeline','octopus_space','octopus_project','octopus_environment','namespace','pod'))
    elements[key]['spec']['links']=[{'title':'Открыть причины','url':P.get('dashboard_path','/grafana/d/cicd-operational-health')+'?viewPanel='+str(elements[reason]['spec']['id'])+'&from=${__from}&to=${__to}&'+link_vars}]
    status.append((key,8,5))
for system in reasonkeys:status.append((reasonkeys[system],8,5))

S='namespace=~"'+NS+'"'
PODS=S+',namespace=~"${namespace:regex}",pod=~"${pod:regex}"'
PVC=S
USED='max by(namespace,persistentvolumeclaim)(kubelet_volume_stats_used_bytes{'+PVC+'})'
AVAIL='max by(namespace,persistentvolumeclaim)(kubelet_volume_stats_available_bytes{'+PVC+'})'
CAP='max by(namespace,persistentvolumeclaim)(kubelet_volume_stats_capacity_bytes{'+PVC+'})'
PCT='100*'+USED+'/('+CAP+'>0)'
NEXUS='min(max by(namespace,pod)(kube_pod_status_ready{namespace="cicd-mia-899",pod=~".*nexus.*",condition="true"}))'
nskey=panel('Nexus — Pod Ready',[('A',prom(NEXUS,instant=True),False)],'stat',desc='Nexus в CI namespace; отсутствие метрики = нет данных.',thresholds=[{'color':'red','value':None},{'color':'green','value':1}],options={'colorMode':'background'})
status.append((nskey,6,5))
status.append((panel('PVC — заполнение сейчас',[('A',prom(PCT,'{{namespace}} · {{persistentvolumeclaim}}',True),False)],'bargauge',unit='percent',desc='По каждому PVC CI-контура. Warning 80%, critical 90%.',thresholds=[{'color':'green','value':None},{'color':'orange','value':80},{'color':'red','value':90}]),10,5))
# Sum of linear slopes, after deduplicating kubelet scraping series.
SLOPE='max by(namespace,persistentvolumeclaim)(deriv(kubelet_volume_stats_available_bytes{'+PVC+'}[6h]))'
status.append((panel('PVC — до заполнения, линейная оценка',[('A',prom(AVAIL+'/(-('+SLOPE+'<0))','{{persistentvolumeclaim}}',True),False)],'stat','s',desc='available / темп убывания за 6h. При отсутствии убывания прогноз не выводится; это не SLA.',thresholds=[{'color':'red','value':None},{'color':'orange','value':86400},{'color':'green','value':604800}]),8,5))
for system,sample in [('teamcity',13),('jenkins',18),('octopus',23)]:
    norm=N[system];root=norm+' | map(select((.status|success) or (.status|degraded))) | if length==0 then empty else point((map(select(.status|success))|length)/length*100) end'
    status.append((panel(LABEL[system]+(' · Prod' if system=='octopus' else '')+' — успешность последних 100',[('A',source(system,'D',root),False)],'stat','percent',desc='Текущая выборка; только SUCCESS/FAILURE/UNSTABLE/TimedOut, не процент за выбранный период.'),8,4))

# Current executions with baseline grouped by pipeline, not global mean.
for system in ('teamcity','jenkins','octopus'):
    running=N[system]
    if system=='teamcity':running='complete_inventory("build") | {build:.} | '+running
    elif system=='octopus':running='complete_inventory("Items") | {Items:.} | '+running
    elif system=='jenkins':running=running.replace('map(select(.status != "RUNNING")) | sort_by(.finish) | reverse | .[:100]', 'map(select(.status=="RUNNING"))')
    root=running+' | .[] | {workflow,status,age:(if .start==null then null else now-.start end),agent:(.agent//"Не передано API"),url}'
    a=source(system,'E',root,True,[col('workflow','Пайплайн'),col('status','Результат'),col('agent','Сервер обработки' if system=='octopus' else 'Агент'),col('age','Возраст','number'),col('url','URL')])
    b=history(system,tail=' | map(select((.status|success) and .duration!=null and .duration>0)) | group_by(.workflow) | .[] | select(length>=3) | {workflow:.[0].workflow,usual:(map(.duration|select(.!=null and .>0))|mean)}',columns=[col('workflow','Пайплайн'),col('usual','Обычно','number')])
    trs=[{'kind':'joinByField','spec':{'id':'joinByField','options':{'byField':'Пайплайн','mode':'outerTabular'}}},{'kind':'filterByValue','spec':{'id':'filterByValue','options':{'filters':[{'fieldName':'Возраст','config':{'id':'isNotNull','options':{}}}],'match':'all','type':'include'}}},{'kind':'calculateField','spec':{'id':'calculateField','options':{'mode':'binary','binary':{'left':'Возраст','right':'Обычно','operator':'/'},'alias':'Относительно обычного'}}}]
    ov=[override('Возраст',{'unit':'s'}),override('Обычно',{'unit':'s'}),override('Относительно обычного',{'thresholds':{'mode':'absolute','steps':[{'color':'green','value':None},{'color':'orange','value':2},{'color':'red','value':3}]},'custom.cellOptions':{'type':'color-background'}}),override('URL',{'links':[{'url':'${__value.raw}','title':'Открыть запуск','targetBlank':True}]})]
    current.append((panel(LABEL[system]+' — текущие выполнения',[('A',a,False),('B',b,False)],desc='Возраст и средняя успешных запусков того же pipeline (≥3). Jenkins timestamp включает queue wait; agent может отсутствовать у Pipeline.',overrides=ov,transforms=trs),24,8))
    # Queue detail count and oldest age are present in one table; no repeated KPI tiles.
    if system=='teamcity':root='complete_inventory("build") | .[] | {workflow:.buildTypeId,id:.id,age:(if .queuedDate==null then null else now-(.queuedDate|epoch) end),reason:(.waitReason//"Не передано API"),url:.webUrl}'
    elif system=='jenkins':root='arr("items") | "${jenkins_pipeline:raw}" as $jobPath | if $jobPath=="*" then . else map(select(.task.fullName!=null)|select(("job/"+(.task.fullName|split("/")|map(@uri)|join("/job/")))==$jobPath)) end | .[] | {workflow:.task.fullName,id:.id,age:(if .inQueueSince==null then null else now-(.inQueueSince/1000) end),reason:(.why//"Не передано API"),url:.task.url}'
    else:root='complete_inventory("Items") | .[] | {workflow:(.ProjectId//.Arguments.ProjectId//.Description),id:.Id,age:(if .QueueTime==null then null else now-(.QueueTime|epoch) end),reason:(.ErrorMessage//(if .HasPendingPreconditions then "Pending preconditions" elif .HasPendingInterruptions then "Pending interruptions" else "Не передано API" end)),url:((.Links.Web//("/app#/${octopus_space}/tasks/"+.Id))|if startswith("http") then . else "http://octopus.admortgage.com"+. end)}'
    qr=source(system,'B','['+root+'] | {count:length,oldest:(map(.age|select(.!=null))|max),reason:(map(.reason)|unique|join("; "))}',True,[col('count','Задач','number'),col('oldest','Старейшая задача','number'),col('reason','Причины ожидания')])
    current.append((panel(LABEL[system]+' — очередь: задачи и причины',[('A',qr,False)],desc='Размер очереди, возраст старейшей задачи и причины. Отсутствующий timestamp не заменяется нулём.',overrides=[override('Старейшая задача',{'unit':'s'})]),12,7))
    agroot={'teamcity':'complete_inventory("agent") | map(select(.connected and .authorized and .enabled)) | {busy:([.[]|select(.build!=null)]|length),total:length} | {load:((.busy|tostring)+" из "+(.total|tostring))}',
            'jenkins':'arr("computer") | map(select(.numExecutors>0 and .offline==false and .temporarilyOffline==false)) | {busy:([.[]|select(.idle==false)]|length),total:length} | {load:((.busy|tostring)+" из "+(.total|tostring))}',
            'octopus':'complete_inventory("Items") | {healthy:([.[]|select(.HealthStatus=="Healthy" and .IsDisabled==false)]|length),total:([.[]|select(.IsDisabled==false)]|length)} | {load:((.healthy|tostring)+" здоровых из "+(.total|tostring)+" (busy неизвестно)")}' }[system]
    current.append((panel(LABEL[system]+(' — состояние workers' if system=='octopus' else ' — занято агентов'),[('A',source(system,'C',agroot,True,[col('load','Загрузка')]),False)],desc='Jenkins: busy agents по idle, не busy executor slots. Octopus HealthStatus не означает занятость worker.'),12,7))

# Prod views: the environment selector only lists exact Prod/Production names.
OCBASE='http://octopus.admortgage.com/api/${octopus_space}'
prodroot=N['octopus']+' | map(select(.workflow!="MISSING_PROJECT")) | group_by(.workflow) | .[] | max_by(.start//.finish//0) | {project:.workflow,environment:"Prod",status,url,start,finish,version:(.branch//"Не передано API"),trigger:(.trigger//"Не передано API")}'
prod=source('octopus','D',prodroot,True,[col('project','Проект'),col('environment','Окружение'),col('status','Результат'),col('version','Релиз / контекст'),col('trigger','Кто запустил'),col('finish','Завершение','timestamp_epoch_s'),col('url','URL')])
prodkey=panel('Octopus — последний Prod-деплой по проекту (выборка)',[('A',prod,False)],desc='Последний видимый deployment task в Prod/Production; ≤100 tasks не гарантируют присутствие всех проектов.',overrides=table_links());deploy.append((prodkey,24,9))
# Exact 24h/selected-range filter is sent to the server, with full-inventory check.
daily=source('octopus','D', 'complete_inventory("Items") | {Items:.} | '+N['octopus']+' | map(select(.status|failed) | select(.finish!=null and .finish>=now-86400 and .finish<=now)) | .[] | {workflow,id,status,finish,url,trigger:(.trigger//"Не передано API")}',True,[col('workflow','Проект'),col('id','Номер'),col('status','Результат'),col('finish','Завершение','timestamp_epoch_s'),col('trigger','Кто запустил'),col('url','URL')])
param(daily,'take','1000');param(daily,'fromCompletedDate','${prod_from24h:raw}')
deploy.append((panel('Octopus — падения Prod за 24h, текущий snapshot',[('A',daily,False)],desc='Скользящие 24h от текущего времени, независимо от timepicker. API ограничивает выборку; jq проверяет окно и truncation.',overrides=table_links()),24,8))
for system in ('teamcity','jenkins','octopus'):
    problems.append((results(system,True),24,8))
    top=history(system,tail=' | map(select(.workflow!="MISSING_PROJECT")) | ranked | .[]',columns=[col('workflow','Пайплайн'),col('failures','Падения','number'),col('mean','Средняя длительность','number'),col('max','Максимальная длительность','number'),col('samples','Выборка','number'),col('alternations','Чередования','number'),col('suspected_flaky','Похоже на flaky','boolean')])
    problems.append((panel(LABEL[system]+(' · Prod' if system=='octopus' else '')+' — проблемные пайплайны',[('A',top,False)],desc='Последние 100. Чередования — эвристика: ≥4 результата и смена success/failure в ≥50% соседних пар; не доказательство flaky.',overrides=[override('Средняя длительность',{'unit':'s'}),override('Максимальная длительность',{'unit':'s'})]),24,8))

# Detail rows contain tables only for application data; no repeated KPI stats.
for system in ('teamcity','jenkins','octopus'):
    members=[(results(system),24,10)]
    if system=='teamcity':
        root='complete_inventory("agent")[] | {name:.name,online:.connected,enabled:.enabled,authorized:.authorized,busy:(.build!=null),reason:(.disconnectionComment//"")}'
        warn=extra(system,'/app/rest/health','arr("healthItem")[] | {severity:.severity,identity:.identity,message:(.healthCategory.description//.healthCategory.name)}',{'locator':'global:true,minSeverity:warn','fields':'healthItem(identity,severity,healthCategory(name,description))'},[col('severity','Severity'),col('identity','ID'),col('message','Предупреждение')])
        project_warn=copy.deepcopy(warn);param(project_warn,'locator','project:(affectedProject:_Root),minSeverity:warn')
        members.append((panel('TeamCity — server health warnings',[('A',warn,False),('B',project_warn,False)],desc='Global и project warnings, включая VCS. Нужны права Server Health; API error не означает отсутствие проблем.',transforms=[{'kind':'merge','spec':{'id':'merge','options':{}}}]),24,7))
    elif system=='jenkins':root='arr("computer") | map(select(.numExecutors>0))[] | {name:.displayName,online:(.offline|not),enabled:(.temporarilyOffline|not),reason:(.offlineCauseReason//""),idle:.idle,executors:.numExecutors}'
    else:root='complete_inventory("Items")[] | {name:.Name,health:.HealthStatus,enabled:(.IsDisabled|not),pools:(.WorkerPoolIds|join(","))}'
    members.append((panel(LABEL[system]+' — агенты'+(' / workers' if system=='octopus' else ''),[('A',source(system,'C',root,True,[]),False)],desc='Инвентарь, состояние и причины из API.'),24,8))
    if system=='octopus':members.append((panel('Octopus — Tentacle targets и health checks',[('A',extra(system,OCBASE+'/machines','complete_inventory("Items")[] | {name:.Name,health:.HealthStatus,disabled:.IsDisabled,status:(.StatusSummary//"Не передано API")}',{'take':'1000'},[]),False)],desc='Health targets — отдельно от workers; kube namespace Tentacle контролируется ниже.'),24,8))
    details.append(layoutrow(LABEL[system]+' · details',members,True))
infra=[]
# Clear legends map known CI server prefixes; unmatched pods retain their raw name.
def friendly(expr):
    expr='label_replace('+expr+',"component","Octopus","pod","(?i).*octopus.*")'
    return 'label_replace(label_replace(label_replace(label_replace('+expr+',"component","TeamCity","pod","(?i).*teamcity.*"),"component","Jenkins","pod","(?i).*jenkins.*"),"component","Nexus","pod","(?i).*nexus.*"),"component","Tentacle","namespace","octopus-worker-mia-899")'
ready='max by(namespace,pod)(kube_pod_status_ready{'+PODS+',condition="true"})'
readykey=panel('CI pods — Ready',[('A',prom(friendly(ready),'{{component}} · {{pod}}'),False)],'state-timeline',desc='Только cicd-mia-899 и octopus-worker-mia-899. Unknown prefix сохраняет pod name.',thresholds=[{'color':'red','value':None},{'color':'green','value':1}])
elements[readykey]['spec']['vizConfig']['spec']['fieldConfig']['defaults']['mappings']=[{'type':'value','options':{'0':{'text':'NOT READY','color':'red'},'1':{'text':'READY','color':'green'}}}]
elements[readykey]['spec']['vizConfig']['spec']['options']={'showValue':'auto','rowHeight':0.85,'mergeValues':True,'legend':{'displayMode':'list','placement':'bottom'},'tooltip':{'mode':'single'}}
infra.append((readykey,24,7))
for resource,metric,unit,ksm in [('CPU','rate(container_cpu_usage_seconds_total','cores','cpu'),('Память','container_memory_working_set_bytes','bytes','memory')]:
    usage=('sum by(namespace,pod)(rate(container_cpu_usage_seconds_total{'+PODS+',container!="",container!="POD"}[$__rate_interval]))' if resource=='CPU' else 'sum by(namespace,pod)(container_memory_working_set_bytes{'+PODS+',container!="",container!="POD"})')
    qs=[('A',prom(friendly(usage),'{{component}} · {{pod}} usage'),False)]
    for ref,kind in [('B','requests'),('C','limits')]:qs.append((ref,prom(friendly('sum by(namespace,pod)(max by(namespace,pod,container)(kube_pod_container_resource_'+kind+'{'+PODS+',resource="'+ksm+'"}))'),'{{component}} · {{pod}} '+kind),False))
    infra.append((panel(resource+' CI pod — usage / requests / limits',qs,'timeseries',unit,desc='Абсолютные ресурсы. Requests/limits — сумма контейнеров; отсутствующий limit не заменяется нулём.'),24,8))
infra.append((panel('CI pods — рестарты за rolling 15m',[('A',prom(friendly('sum by(namespace,pod)(increase(kube_pod_container_status_restarts_total{'+PODS+'}[15m]))'),'{{component}} · {{pod}}'),False)],'timeseries',thresholds=[{'color':'green','value':None},{'color':'orange','value':1},{'color':'red','value':3}],desc='Warning 1, critical 3 за 15m; больше нет дефолта 80.'),24,7))
infra.append((panel('PVC — прогноз свободного места через 7d',[('A',prom('min by(namespace,persistentvolumeclaim)(predict_linear(kubelet_volume_stats_available_bytes{'+PVC+'}[6h],604800))','{{persistentvolumeclaim}}'),False)],'timeseries','bytes',desc='Линейный прогноз по 6h. Значение ≤0 означает риск заполнения в горизонте 7d.'),24,7))
details.append(layoutrow('Kubernetes / PVC · details',infra,True))
for v in variables:
    v['spec'].setdefault('allowCustomValue',False)
    if v['kind']=='QueryVariable':v['spec'].setdefault('regex','')
rows=[layoutrow('1 Статус — текущий REST snapshot, не выбранный период',status),layoutrow('2 Сейчас — выполнения, очереди, агенты',current),layoutrow('3 Деплои — только Prod',deploy),layoutrow('4 Проблемы — последние результаты и проблемные пайплайны',problems),layoutrow('5 Details',[],nested={'kind':'RowsLayout','spec':{'rows':details}})]
annotations=[{'kind':'AnnotationQuery','spec':{'name':'Pod restarts','enable':True,'hide':False,'iconColor':'orange','query':prom('sum by(namespace,pod)(increase(kube_pod_container_status_restarts_total{'+S+'}[5m]))>0','Restart {{pod}}')}}]
aquery=source('octopus','D',N['octopus']+' | .[] | select(.finish!=null) | {time:(.finish*1000),title:("Prod deploy "+.workflow),text:(.status+" · "+(.url//"")),tags:"Prod,Octopus"}',True,[col('time','time','timestamp_epoch'),col('title'),col('text'),col('tags')])
param(aquery,'fromCompletedDate','${__timeFrom:date:iso}');param(aquery,'toCompletedDate','${__timeTo:date:iso}');param(aquery,'take','1000')
annotations.append({'kind':'AnnotationQuery','spec':{'name':'Prod deployments','enable':True,'hide':False,'iconColor':'blue','query':aquery}})
doc={'apiVersion':'dashboard.grafana.app/v2','kind':'Dashboard','metadata':{'name':'cicd-operational-health'},'spec':{'title':'CI/CD Operational Health','editable':True,'cursorSync':'Off','liveNow':False,'preload':False,'tags':['cicd','review','infinity','prometheus'],'timeSettings':P['metadata']['timeSettings'],'variables':variables,'links':[],'annotations':annotations,'elements':elements,'layout':{'kind':'RowsLayout','spec':{'rows':rows}}}}
(OUT/'cicd-health-dashboard.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n')
print(len(elements),'panels; 5 primary rows; 4 collapsed detail rows; Grafana v2')
