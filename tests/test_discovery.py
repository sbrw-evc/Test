import importlib.util,json,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('discovery',Path(__file__).resolve().parents[1]/'scripts/discover_pipelines.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
CONFIG={'octopus_space':'Spaces-1','octopus_environment':'Environments-1','discovery':{'jenkins_name_regex':r'platform/(?P<pipeline>[^/]+)(?:/.*)?'}}
def fixture():
 return {'teamcity':[{'id':'Backend_Build','name':'Build','project':{'id':'Backend','name':'Backend'}}],
         'jenkins':[{'fullName':'platform/backend/build.one','name':'build.one'},{'fullName':'platform/backend/test','name':'test'}],
         'octopus_projects':[{'Id':'Projects-1','Name':'Backend','SpaceId':'Spaces-1'}],
         'octopus_environments':[{'Id':'Environments-1','Name':'Production','SpaceId':'Spaces-1'}]}
class FakeClient:
 def __init__(self,pages):self.pages=pages;self.calls=[]
 def get(self,path,params=None):self.calls.append((path,params));return self.pages[len(self.calls)-1]
class Tests(unittest.TestCase):
 def test_exact_mapping_and_regex(self):
  import re
  r=m.resolve(CONFIG,fixture());p=r['resolved'][0]
  self.assertFalse(r['unresolved']);self.assertEqual(p['octopus_project'],'Projects-1')
  self.assertTrue(re.fullmatch(p['jenkins_job_regex'],'platform/backend/build.one'))
  self.assertFalse(re.fullmatch(p['jenkins_job_regex'],'platform/backend/buildXone'))
 def test_successful_write_and_second_run(self):
  import yaml
  with tempfile.TemporaryDirectory() as temp:
   path=Path(temp)/'config.yml';inp=Path(temp)/'inventory.json';report=Path(temp)/'report.json'
   path.write_text(yaml.safe_dump(CONFIG));inp.write_text(json.dumps(fixture()))
   args=['--config',str(path),'--inventory',str(inp),'--report',str(report),'--write']
   self.assertEqual(m.main(args),0);first=path.read_bytes()
   self.assertEqual(m.main(args),0);self.assertEqual(path.read_bytes(),first)
   self.assertEqual(len(yaml.safe_load(first)['pipelines']),1)
 def test_ambiguous_octopus(self):
  i=fixture();i['octopus_projects'].append({'Id':'Projects-2','Name':'backend','SpaceId':'Spaces-1'})
  r=m.resolve(CONFIG,i);self.assertFalse(r['resolved']);self.assertIn('ambiguous',r['unresolved'][0]['reasons'][0])
 def test_explicit_metadata(self):
  i=fixture();i['teamcity'][0]['parameters']={'property':[{'name':'monitoring.octopus_project','value':'Projects-1'},{'name':'monitoring.jenkins_job_regex','value':'platform/backend/.*'}]}
  i['octopus_projects'][0]['Name']='Different name';i['teamcity'][0]['project']['name']='Other project'
  self.assertEqual(len(m.resolve(CONFIG,i)['resolved']),1)
 def test_missing_environment(self):
  i=fixture();i['octopus_environments']=[];self.assertFalse(m.resolve(CONFIG,i)['resolved'])
 def test_missing_jenkins(self):
  i=fixture();i['jenkins']=[];self.assertFalse(m.resolve(CONFIG,i)['resolved'])
 def test_duplicate_tc_project_name(self):
  i=fixture();i['teamcity'].append({'id':'Other_Build','name':'Build','project':{'id':'Other','name':'backend'}})
  self.assertFalse(m.resolve(CONFIG,i)['resolved'])
 def test_overrides(self):
  c={**CONFIG,'discovery':{**CONFIG['discovery'],'overrides':{'Backend_Build':{'pipeline_id':'stable-backend','pipeline_name':'My pipeline'}}}}
  p=m.resolve(c,fixture())['resolved'][0];self.assertEqual(p['id'],'stable-backend')
 def test_tc_pagination(self):
  c=FakeClient([{'count':1,'buildType':[{'id':'1'}],'nextHref':'/app/rest/buildTypes?start=1'},{'count':1,'buildType':[{'id':'2'}]}])
  self.assertEqual(len(m.paged_tc(c,'count:1','id')),2)
 def test_tc_page_incomplete(self):
  with self.assertRaises(m.DiscoveryError):m.paged_tc(FakeClient([{'count':2,'buildType':[{}]}]),'count:1','id')
 def test_octopus_pagination(self):
  c=FakeClient([{'TotalResults':2,'Items':[{'Id':'1'}]},{'TotalResults':2,'Items':[{'Id':'2'}]}]);self.assertEqual(len(m.paged_octopus(c,'projects')),2);self.assertEqual(c.calls[1][1]['skip'],1)
 def test_octopus_changed(self):
  with self.assertRaises(m.DiscoveryError):m.paged_octopus(FakeClient([{'TotalResults':2,'Items':[{}]},{'TotalResults':3,'Items':[{}]}]),'projects')
 def test_origin_guard(self):
  client=m.Client('https://example.com/teamcity',{})
  for path in ['https://evil.example/app/rest','/other/path']:
   with self.assertRaises(m.DiscoveryError):client.get(path)
 def test_unresolved_keeps_config(self):
  import yaml
  with tempfile.TemporaryDirectory() as temp:
   path=Path(temp)/'config.yml';i=fixture();i['jenkins']=[];inp=Path(temp)/'inventory.json';inp.write_text(json.dumps(i));path.write_text(yaml.safe_dump(CONFIG));before=path.read_bytes()
   code=m.main(['--config',str(path),'--inventory',str(inp),'--report',str(Path(temp)/'report.json'),'--write'])
   self.assertEqual(code,1);self.assertEqual(path.read_bytes(),before)
 def test_report_excludes_arbitrary_parameters(self):
  i=fixture();i['teamcity'][0]['parameters']={'property':[{'name':'password','value':'SHOULD_NOT_APPEAR'}]}
  self.assertNotIn('SHOULD_NOT_APPEAR',json.dumps(m.resolve(CONFIG,i)))
if __name__=='__main__':unittest.main()
