import json,subprocess,unittest,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
D=json.loads((ROOT/'cicd-health/cicd-health-dashboard.json').read_text())
S=D['spec']
def substitute(q,mappings=None,encoded=True):
    for k,v in {'jenkins_pipeline':'*','octopus_space':'Spaces-1','octopus_project':'Projects-1','octopus_environment':'Environments-1'}.items():q=q.replace('${'+k+':raw}',v).replace('${'+k+'}',v)
    for name in ('octopus_metadata','jenkins_agent_map','octopus_project_names'):
        value=(mappings or {}).get(name,{})
        q=q.replace('${'+name+':json}',json.dumps(json.dumps(value) if encoded else value))
    return q
class ReviewTests(unittest.TestCase):
 def jq(self,q,data,mappings=None,encoded=True):
  r=subprocess.run(['jq','-c',substitute(q,mappings,encoded)],input=json.dumps(data),text=True,capture_output=True)
  self.assertEqual(r.returncode,0,r.stderr)
  return [json.loads(x) for x in r.stdout.splitlines() if x]
 def test_five_rows(self):self.assertEqual(len(S['layout']['spec']['rows']),5)
 def test_nested_details_collapsed(self):
  rows=S['layout']['spec']['rows'][-1]['spec']['layout']['spec']['rows'];self.assertEqual(len(rows),4);self.assertTrue(all(r['spec']['collapse'] for r in rows))
 def test_no_timezone_or_header_links(self):
  self.assertNotIn('display_timezone',[v['spec']['name'] for v in S['variables']]);self.assertFalse(S['links'])
 def test_scope_and_storage(self):
  exprs=[q['spec']['query']['spec'].get('expr','') for e in S['elements'].values() for q in e['spec']['data']['spec']['queries']]
  self.assertTrue(any('predict_linear' in q for q in exprs));self.assertTrue(any('kubelet_volume_stats_used_bytes' in q for q in exprs))
  for e in exprs:
   if e:self.assertTrue('cicd-mia-899' in e or 'octopus-worker-mia-899' in e,e)
 def test_row_refs_and_overlap(self):
  def check(layout):
   if layout['kind']=='RowsLayout':
    for row in layout['spec']['rows']:check(row['spec']['layout'])
   else:
    items=[i['spec'] for i in layout['spec']['items']]
    for i,a in enumerate(items):
     self.assertIn(a['element']['name'],S['elements'])
     for b in items[i+1:]:self.assertFalse(a['x']<b['x']+b['width'] and a['x']+a['width']>b['x'] and a['y']<b['y']+b['height'] and a['y']+a['height']>b['y'])
  check(S['layout'])
 def test_queue_zero_is_valid(self):
  for e in S['elements'].values():
   if '— очередь: задачи' not in e['spec']['title']:continue
   q=e['spec']['data']['spec']['queries'][0]['spec']['query']['spec'];system=q['url']
   data={'build':[]} if '/buildQueue' in system else {'items':[]} if '/queue/' in system else {'Items':[],'TotalResults':0}
   rows=self.jq(q['root_selector'],data);self.assertEqual(rows[0]['count'],0)
 def test_rank_switches(self):
  e=next(e for e in S['elements'].values() if e['spec']['title']=='TeamCity — проблемные пайплайны')
  q=e['spec']['data']['spec']['queries'][0]['spec']['query']['spec']['root_selector']
  b=[{'id':i,'buildTypeId':'A','status':s,'state':'finished','startDate':f'20261012T10000{i}+0300','finishDate':f'20261012T10001{i}+0300','webUrl':'https://example.test/build'} for i,s in enumerate(['SUCCESS','FAILURE','SUCCESS','FAILURE'])]
  rows=self.jq(q,{'build':b});self.assertEqual(rows[0]['failures'],2);self.assertEqual(rows[0]['alternations'],3);self.assertTrue(rows[0]['suspected_flaky'])
 def test_deployment_metadata_lookup(self):
  e=next(e for e in S['elements'].values() if 'Octopus — последний Prod-деплой' in e['spec']['title'])
  q=e['spec']['data']['spec']['queries'][0]['spec']['query']['spec']['root_selector']
  mapping={'Task-1':{'project':'Projects-1','version':'Releases-5','trigger':'deploy-user'}}
  q=q.replace('${octopus_metadata:json}',json.dumps(json.dumps(mapping)))
  data={'Items':[{'Id':'Task-1','Name':'Deploy','State':'Success','StartTime':'2026-10-12T10:00:00Z','CompletedTime':'2026-10-12T10:05:00Z','Description':'Deploy app'}]}
  rows=self.jq(q,data);self.assertEqual(rows[0]['project'],'Projects-1');self.assertEqual(rows[0]['trigger'],'deploy-user');self.assertEqual(rows[0]['version'],'Releases-5')
 def test_invalid_date_is_not_dropped(self):
  e=next(e for e in S['elements'].values() if e['spec']['title']=='TeamCity — проблемные пайплайны')
  q=e['spec']['data']['spec']['queries'][0]['spec']['query']['spec']['root_selector']
  r=subprocess.run(['jq','-c',substitute(q)],input=json.dumps({'build':[{'id':1,'buildTypeId':'A','status':'FAILURE','startDate':'bad','finishDate':'bad'}]}),text=True,capture_output=True)
  self.assertNotEqual(r.returncode,0)
 def test_prod_environment_guard(self):
  e=next(e for e in S['elements'].values() if 'Octopus — последний Prod-деплой' in e['spec']['title'])
  q=e['spec']['data']['spec']['queries'][0]['spec']['query']['spec'];self.assertTrue(any(p['key']=='environment' for p in q['url_options']['params']))
  r=subprocess.run(['jq','-c',substitute(q['root_selector'].replace('${octopus_environment:raw}','__select_prod__'))],input='{"Items":[]}',text=True,capture_output=True);self.assertNotEqual(r.returncode,0)
 def test_transformation_schema(self):
  for e in S['elements'].values():
   for t in e['spec']['data']['spec']['transformations']:
    self.assertEqual(t['kind'],'Transformation');self.assertIn('group',t);self.assertNotIn('id',t['spec'])
 def test_v2_links(self):
  for e in S['elements'].values():
   for link in e['spec']['links']:
    self.assertTrue(set(link)<=set(['title','url','targetBlank']))
    self.assertIn('${teamcity_pipeline:queryparam}',link['url'])
    self.assertIn('/grafana/d/',link['url'])
 def test_annotations(self):self.assertEqual(len(S['annotations']),2)
 def test_reduce_backend_contract(self):
  # Grafana UnmarshalReduceCommand: strict is the default when mode is absent.
  # Explicit modes are only dropNN and replaceNN; mode='strict' rejects the query.
  reduced=[]
  for e in S['elements'].values():
   for q in e['spec']['data']['spec']['queries']:
    spec=q['spec']['query']['spec']
    if spec.get('type')=='reduce':
     reduced.append(spec)
     self.assertNotIn('settings',spec)
     self.assertEqual(spec['reducer'],'last')
  self.assertEqual(len(reduced),18)
  alerts=(ROOT/'cicd-health/grafana-alert-rules.yml').read_text()
  self.assertNotIn('mode: strict',alerts)
  self.assertIn('reducer: last',alerts)
 def test_metadata_interpolation_object_and_string(self):
  maps={'octopus_metadata':{'Task-1':{'project':'Projects-1','version':'1.2.3','trigger':'deploy-user'}},'octopus_project_names':{'Projects-1':'App "quoted" \\ path'},'jenkins_agent_map':{'https://jenkins.example/job/app/7/':'agent "one" \\ runner'}}
  fixtures={
   'Jenkins — текущие выполнения':{'jobs':[{'fullName':'app','builds':[{'number':7,'building':True,'timestamp':1700000000000,'duration':0,'url':'https://jenkins.example/job/app/7/'}]}]},
   'Octopus — текущие выполнения':{'Items':[{'Id':'Task-1','Name':'Deploy','State':'Executing','StartTime':'2026-10-12T10:00:00Z','CompletedTime':None}], 'TotalResults':1}}
  for encoded in (True,False):
   for title,data in fixtures.items():
    with self.subTest(title=title,encoded=encoded):
     e=next(e for e in S['elements'].values() if e['spec']['title']==title)
     q=e['spec']['data']['spec']['queries'][0]['spec']['query']['spec']['root_selector']
     rows=self.jq(q,data,maps,encoded);self.assertEqual(len(rows),1)
     if title.startswith('Jenkins'):self.assertEqual(rows[0]['agent'],maps['jenkins_agent_map']['https://jenkins.example/job/app/7/'])
     else:self.assertEqual(rows[0]['workflow'],maps['octopus_project_names']['Projects-1']+' [Projects-1]')
 def test_success_rate_with_interpolated_object(self):
  fixtures={
   'Jenkins — успешность последних 100':{'jobs':[{'fullName':'app','builds':[{'number':7,'building':False,'result':'SUCCESS','timestamp':1700000000000,'duration':10000,'url':'https://jenkins.example/job/app/7/'}]}]},
   'Octopus · Prod — успешность последних 100':{'Items':[{'Id':'Task-1','ProjectId':'Projects-1','Name':'Deploy','State':'Success','StartTime':'2026-10-12T10:00:00Z','CompletedTime':'2026-10-12T10:05:00Z'}]}}
  for title,data in fixtures.items():
   e=next(e for e in S['elements'].values() if e['spec']['title']==title)
   q=e['spec']['data']['spec']['queries'][0]['spec']['query']['spec']['root_selector']
   rows=self.jq(q,data,encoded=False);self.assertEqual(rows[0]['value'],100)
 def test_malformed_metadata_is_error(self):
  e=next(e for e in S['elements'].values() if e['spec']['title']=='Octopus — текущие выполнения')
  q=e['spec']['data']['spec']['queries'][0]['spec']['query']['spec']['root_selector']
  for bad in ('[]','null','42','"not json"'):
   r=subprocess.run(['jq','-c',substitute(q.replace('${octopus_project_names:json}',bad))],input='{"Items":[],"TotalResults":0}',text=True,capture_output=True)
   self.assertNotEqual(r.returncode,0)
 def test_reason_frames_are_merged(self):
  for e in S['elements'].values():
   if '— причины состояния' in e['spec']['title']:self.assertEqual(e['spec']['data']['spec']['transformations'][0]['group'],'merge')
 def test_pvc_percent_scale(self):
  e=next(e for e in S['elements'].values() if e['spec']['title']=='PVC — заполнение сейчас')
  viz=e['spec']['vizConfig']['spec'];self.assertEqual(viz['fieldConfig']['defaults']['min'],0);self.assertEqual(viz['fieldConfig']['defaults']['max'],100);self.assertEqual(viz['options']['orientation'],'horizontal')
 def test_queries_compile(self):
  for e in S['elements'].values():
   for q in e['spec']['data']['spec']['queries']:
    body=q['spec']['query']['spec'].get('root_selector')
    if not body:continue
    # jq parses the entire program even though an empty input avoids runtime shape checks.
    r=subprocess.run(['jq','-n','empty | ('+substitute(body)+')'],text=True,capture_output=True);self.assertEqual(r.returncode,0,r.stderr)
if __name__=='__main__':unittest.main()
