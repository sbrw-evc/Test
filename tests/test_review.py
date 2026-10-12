import json,subprocess,unittest,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
D=json.loads((ROOT/'cicd-health/cicd-health-dashboard.json').read_text())
S=D['spec']
def substitute(q):
    for k,v in {'jenkins_pipeline':'*','octopus_space':'Spaces-1','octopus_project':'Projects-1','octopus_environment':'Environments-1'}.items():q=q.replace('${'+k+':raw}',v).replace('${'+k+'}',v)
    return q.replace("${octopus_metadata:json}",json.dumps("{}")).replace("${jenkins_agent_map:json}",json.dumps("{}")).replace("${octopus_project_names:json}",json.dumps("{}"))
class ReviewTests(unittest.TestCase):
 def jq(self,q,data):
  r=subprocess.run(['jq','-c',substitute(q)],input=json.dumps(data),text=True,capture_output=True)
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
 def test_queries_compile(self):
  for e in S['elements'].values():
   for q in e['spec']['data']['spec']['queries']:
    body=q['spec']['query']['spec'].get('root_selector')
    if not body:continue
    # jq parses the entire program even though an empty input avoids runtime shape checks.
    r=subprocess.run(['jq','-n','empty | ('+substitute(body)+')'],text=True,capture_output=True);self.assertEqual(r.returncode,0,r.stderr)
if __name__=='__main__':unittest.main()
