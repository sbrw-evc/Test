"""Fixed alert scopes are independent of dashboard variable selections."""
from pathlib import Path
import copy,json,re,yaml,sys
from urllib.parse import quote
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'cicd-health'
C=yaml.safe_load((ROOT/'monitoring-config.example.yml').read_text())
if len(sys.argv)>1 and Path(sys.argv[1]).is_file():C=yaml.safe_load(Path(sys.argv[1]).read_text())
P=json.loads((OUT/'review-source-profile.json').read_text());rules=[]
VALUES={'teamcity_pipeline':C.get('teamcity_pipeline','*'),'jenkins_pipeline':C.get('jenkins_pipeline','*'),'octopus_space':C['octopus_space'],'octopus_project':C.get('octopus_project','*'),'octopus_environment':C['prod_environment_id']}
def fixed(q):
 q=copy.deepcopy(q)
 def macros(s):
  job=VALUES['jenkins_pipeline'].strip('/')
  project=VALUES['octopus_project'];tc=VALUES['teamcity_pipeline']
  s=s.replace('$__combineValues(,/,,${jenkins_pipeline:csv})','' if job=='*' else job+'/')
  s=s.replace('$__combineValues(?project=,,__comma,${octopus_project:csv})','' if project=='*' else '?project='+quote(project,safe=''))
  s=s.replace('$__combineValues(buildType:__openid:,__close__comma,__comma,${teamcity_pipeline:csv})','' if tc=='*' else 'buildType:(id:'+tc+'),')
  if '$__combineValues' in s:raise ValueError('Unresolved alert scope macro')
  return s
 q['spec']['url']=macros(q['spec'].get('url',''))
 params=q['spec'].get('url_options',{}).get('params',[])
 for p in params:p['value']=macros(p['value'])
 def sub(s):
  for key,val in VALUES.items():s=s.replace('${'+key+':raw}',val).replace('${'+key+':csv}',val).replace('${'+key+'}',val)
  return s
 q['spec']['url']=sub(q['spec']['url']);q['spec']['root_selector']=sub(q['spec']['root_selector'])
 for p in params:p['value']=sub(p['value'])
 return q

def add(uid,title,q,condition,hold='0s',severity='warning',paused=False,nodata='Alerting'):
 ds=q['datasource']['name'];model=copy.deepcopy(q['spec']);model['refId']='A';model['datasource']={'uid':ds,'type':q['group']}
 rules.append({'uid':uid,'title':'CI/CD · '+title,'condition':'B','for':hold,'isPaused':paused,'noDataState':nodata,'execErrState':'Alerting','labels':{'domain':'cicd','severity':severity},'annotations':{'summary':title,'__dashboardUid__':'cicd-operational-health'},'data':[{'refId':'A','datasourceUid':ds,'relativeTimeRange':{'from':600,'to':0},'model':model},{'refId':'R','datasourceUid':'__expr__','relativeTimeRange':{'from':0,'to':0},'model':{'refId':'R','type':'reduce','expression':'A','reducer':'last'}},{'refId':'B','datasourceUid':'__expr__','relativeTimeRange':{'from':0,'to':0},'model':{'refId':'B','type':'math','expression':condition}}]})
for system in ('teamcity','jenkins','octopus'):
 for ref,tag,condition,hold,severity in [('D','failures-sample','$R>0','0s','critical' if system=='octopus' else 'warning'),('B','queue','$R>'+str(C['queue_limit']),'10m','warning'),('F','offline','$R>0','5m','warning'),('E','long','$R>'+str(C['deployment_age_limit'] if system=='octopus' else C['build_age_limit']),'5m','warning')]:
  q=copy.deepcopy(P['sources'][system][ref]['query']);q['spec']['root_selector']=P['common']+P['sources'][system][ref]['root']
  if system=='jenkins' and ref=='D':q['spec']['root_selector']=q['spec']['root_selector'].replace('(.status=="FAILURE" or .status=="UNSTABLE")','(.status=="FAILURE")')
  if system=='octopus' and ref in ('B','D','E'):q['spec']['url_options']['params'].append({'key':'environment','value':'${octopus_environment:raw}'})
  add('review-'+system+'-'+tag,system+' '+tag+(' · Prod' if system=='octopus' and ref!='F' else ' · Space' if system=='octopus' else ''),fixed(q),condition,hold,severity,system=='octopus' and C['prod_environment_id'].startswith('__'))
 if system!='octopus':
  q=copy.deepcopy(P['sources'][system]['C']['query']);q['spec']['root_selector']=P['common']+P['sources'][system]['C']['root']
  add('review-'+system+'-capacity',system+' available agents below minimum',fixed(q),'$R<'+str(C['minimum_agents']),'5m')
NS='cicd-mia-899|octopus-worker-mia-899';UID=P['prometheus_uid']
def prom(e):return {'datasource':{'name':UID},'group':'prometheus','spec':{'expr':e,'instant':True,'range':False,'format':'time_series'}}
sel='namespace=~"'+NS+'"'
used='max by(namespace,persistentvolumeclaim)(kubelet_volume_stats_used_bytes{'+sel+'})'
cap='max by(namespace,persistentvolumeclaim)(kubelet_volume_stats_capacity_bytes{'+sel+'})'
for level,n in [('warning',80),('critical',90)]:add('review-pvc-'+level,'PVC usage > '+str(n)+'%',prom('100*'+used+'/('+cap+'>0)'),'$R>'+str(n),'10m',level)
add('review-pvc-prediction','PVC full within 7d, linear 6h estimate',prom('min by(namespace,persistentvolumeclaim)(predict_linear(kubelet_volume_stats_available_bytes{'+sel+'}[6h],604800))'),'$R<0','30m','warning')
add('review-nexus-ready','Nexus pod not Ready',prom('min(max by(namespace,pod)(kube_pod_status_ready{namespace="cicd-mia-899",pod=~".*nexus.*",condition="true"}))'),'$R<1','3m','critical')
add('review-pod-restarts','CI pod restarts >3 in 15m',prom('sum by(namespace,pod)(increase(kube_pod_container_status_restarts_total{'+sel+'}[15m]))'),'$R>3','5m','warning')
add('review-pods-ready','CI running pod not Ready',prom('max by(namespace,pod)(kube_pod_status_ready{'+sel+',condition="true"}) unless on(namespace,pod) max by(namespace,pod)(kube_pod_status_phase{'+sel+',phase=~"Succeeded|Failed"}==1)'),'$R<1','5m','warning',nodata='OK')
# Migration removes only earlier kit-owned rules. Existing unrelated rules stay intact.
old=['oss-service-down','oss-observation-missing','oss-tc-failed','oss-jenkins-failed','oss-deploy-failed','oss-octopus-api','oss-tc-agent','oss-jenkins-agent','oss-jenkins-node-missing','oss-teamcity-agents-low','oss-jenkins-agents-low','oss-teamcity-queue','oss-jenkins-queue','oss-octopus-queue','oss-tc-long','oss-jenkins-long','oss-http-slow','oss-rules-missing']
(OUT/'grafana-alert-rules.yml').write_text(yaml.safe_dump({'apiVersion':1,'deleteRules':[{'orgId':1,'uid':u} for u in old],'groups':[{'orgId':1,'name':'CI-CD Reviewed','folder':'CI-CD Operations','interval':'60s','rules':rules}]},sort_keys=False,allow_unicode=True))
print(len(rules),'fixed example alert rules; Octopus paused until Prod ID configured')
