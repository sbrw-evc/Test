"""Offline contract tests; no live CI/CD or Grafana access required."""
import json
import pathlib
import subprocess
import unittest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAT = json.loads((ROOT/'tests/query-catalog.json').read_text())
NOW = 1780000000

def run(query, fixture, success=True):
    selector = query['root_selector'].replace('now', str(NOW))
    p = subprocess.run(['jq', '-c', selector], input=json.dumps(fixture), text=True, capture_output=True)
    if success and p.returncode:
        raise AssertionError(p.stderr)
    if not success:
        if not p.returncode:
            raise AssertionError('Expected parser error, got '+p.stdout)
        return
    return [json.loads(line) for line in p.stdout.splitlines()]


def tc(status, start, finish):
    import datetime
    def date(value):
        return datetime.datetime.fromtimestamp(value,datetime.timezone.utc).strftime('%Y%m%dT%H%M%S+0000')
    return {'id':1,'buildTypeId':'App_Build','status':status,'startDate':date(start),'finishDate':date(finish),'webUrl':'https://tc/build/1'}


def fixtures():
    import datetime
    date=lambda v:datetime.datetime.fromtimestamp(v,datetime.timezone.utc).isoformat()
    return {
      'teamcity':{'build':[tc('SUCCESS',NOW-100,NOW-50),tc('FAILURE',NOW-200,NOW-100),tc('UNKNOWN',NOW-300,NOW-200)]},
      'jenkins':{'jobs':[{'fullName':'folder/job','builds':[{'number':1,'building':False,'result':'SUCCESS','timestamp':(NOW-100)*1000,'duration':50000,'url':'https://j/1'},{'number':2,'building':False,'result':'FAILURE','timestamp':(NOW-200)*1000,'duration':100000,'url':'https://j/2'},{'number':3,'building':False,'result':'ABORTED','timestamp':(NOW-300)*1000,'duration':100000,'url':'https://j/3'}]}]},
      'octopus':{'Items':[{'Id':'ServerTasks-'+str(i),'Name':'Deploy','ProjectId':'Projects-1','State':s,'StartTime':date(NOW-a),'CompletedTime':date(NOW-b)} for i,s,a,b in [(1,'Success',100,50),(2,'Failed',200,100),(3,'Canceled',300,200)]],'TotalResults':3}}


class Contracts(unittest.TestCase):
    def test_success_ratio_and_recent_failures(self):
        for c,f in fixtures().items():
            with self.subTest(component=c):
                self.assertEqual(run(CAT[c]['success'],f)[0]['value'],50)
                self.assertEqual(run(CAT[c]['sample'],f)[0]['value'],2)
                self.assertEqual(run(CAT[c]['failure'],f)[0]['value'],1)
                self.assertEqual(len(run(CAT[c]['history'],f)),3)

    def test_empty_history_is_not_one_hundred_percent(self):
        for c,key in [('teamcity','build'),('jenkins','jobs'),('octopus','Items')]:
            f={key:[],'TotalResults':0}
            self.assertEqual(run(CAT[c]['success'],f),[])
            self.assertEqual(run(CAT[c]['failure'],f)[0]['value'],0)
            self.assertEqual(run(CAT[c]['duration'],f),[])
            self.assertEqual(run(CAT[c]['age'],f)[0]['value'],0)

    def test_login_json_or_wrong_schema_rejected(self):
        for c in CAT:
            for key in ('success','failure','api_check','queue'):
                with self.subTest(component=c,key=key):
                    run(CAT[c][key],{'message':'Please log in'},False)

    def test_agent_maintenance_and_executor_semantics(self):
        f={'computer':[
          {'displayName':'controller','offline':False,'temporarilyOffline':False,'numExecutors':0,'idle':True},
          {'displayName':'a','offline':False,'temporarilyOffline':False,'numExecutors':2,'idle':False},
          {'displayName':'b','offline':True,'temporarilyOffline':False,'numExecutors':2,'idle':True},
          {'displayName':'c','offline':True,'temporarilyOffline':True,'numExecutors':2,'idle':True}]}
        self.assertEqual(run(CAT['jenkins']['agents'],f)[0]['value'],1)
        self.assertEqual(run(CAT['jenkins']['offline'],f)[0]['value'],1)
        self.assertEqual(run(CAT['jenkins']['idle'],f)[0]['value'],0)
        f={'agent':[{'connected':True,'enabled':True,'authorized':True},{'connected':False,'enabled':True,'authorized':True},{'connected':False,'enabled':False,'authorized':True}]}
        self.assertEqual(run(CAT['teamcity']['agents'],f)[0]['value'],1)
        self.assertEqual(run(CAT['teamcity']['offline'],f)[0]['value'],1)
        f={'Items':[{'HealthStatus':'Healthy','IsDisabled':False},{'HealthStatus':'Unknown','IsDisabled':False},{'HealthStatus':'Unhealthy','IsDisabled':True}],'TotalResults':3}
        self.assertEqual(run(CAT['octopus']['agents'],f)[0]['value'],1)
        self.assertEqual(run(CAT['octopus']['offline'],f)[0]['value'],1)

    def test_incomplete_inventories_rejected(self):
        run(CAT['teamcity']['agents'],{'agent':[],'nextHref':'/app/rest/agents?start=1000'},False)
        run(CAT['octopus']['agents'],{'Items':[],'TotalResults':1001},False)
        run(CAT['jenkins']['success'],{'jobs':[{'fullName':'deeperFolder','jobs':[{'name':'undisclosed','_class':'Folder'}]}]},False)

    def test_timestamp_offsets_and_failure_window(self):
        f={'build':[{'id':1,'buildTypeId':'Build','status':'FAILURE','startDate':'20260528T232500+0300','finishDate':'20260528T233000+0300'}]}
        history=run(CAT['teamcity']['history'],f)
        self.assertEqual(history[0]['duration'],300)
        for c,f in fixtures().items():
            selector=CAT[c]['failure']['root_selector'].replace('now',str(NOW+10000))
            q={**CAT[c]['failure'],'root_selector':selector}
            self.assertEqual(run(q,f)[0]['value'],0)
        f={'Items':[{'Id':'1','Name':'Deploy','State':'Success','StartTime':'2026-05-28T23:25:00.125+03:00','CompletedTime':'2026-05-28T23:30:00.125+03:00'}]}
        self.assertEqual(run(CAT['octopus']['history'],f)[0]['duration'],300)

    def test_running_build_age_and_nested_jenkins(self):
        f={'jobs':[{'fullName':'folder','jobs':[{'fullName':'folder/branch','builds':[{'number':1,'building':True,'result':None,'timestamp':(NOW-2000)*1000,'duration':2000000}]}]}]}
        self.assertEqual(run(CAT['jenkins']['age'],f)[0]['value'],2000)
        self.assertEqual(run(CAT['jenkins']['failure'],f)[0]['value'],0)
        self.assertEqual(run(CAT['jenkins']['success'],f),[])

    def test_dashboard_geometry_and_datasource_contracts(self):
        uids=set()
        for file in (ROOT/'dashboards').glob('*.json'):
            d=json.loads(file.read_text());self.assertNotIn(d['uid'],uids);uids.add(d['uid'])
            ids=[p['id'] for p in d['panels']];self.assertEqual(len(ids),len(set(ids)))
            boxes=[]
            for p in d['panels']:
                b=p['gridPos'];self.assertLessEqual(b['x']+b['w'],24)
                for old in boxes:
                    overlap=(b['x']<old['x']+old['w'] and old['x']<b['x']+b['w'] and b['y']<old['y']+old['h'] and old['y']<b['y']+b['h'])
                    self.assertFalse(overlap,(file.name,p['title']))
                boxes.append(b)
                for q in p.get('targets',[]):
                    self.assertIn(q['datasource']['uid'],{'cicd-prometheus','cicd-teamcity','cicd-jenkins','cicd-octopus','__expr__'})
                    if q['datasource']['uid'].startswith('cicd-') and q.get('type')=='json':
                        self.assertEqual(q['parser'],'jq-backend')
                        self.assertEqual(q['url_options']['method'],'GET')
        self.assertEqual(len(uids),4)

    def test_alerts_use_backend_and_no_dashboard_variables(self):
        d=yaml.safe_load((ROOT/'provisioning/alerting/rules.yaml').read_text())
        rules=d['groups'][0]['rules']
        self.assertGreaterEqual(len(rules),24)
        for rule in rules:
            self.assertEqual(rule['noDataState'],'Alerting')
            self.assertEqual(rule['execErrState'],'Alerting')
            for target in rule['data']:
                q=target['model']
                self.assertNotIn('$namespace',json.dumps(q))
                self.assertNotIn('$pod',json.dumps(q))
                if q.get('type')=='json':
                    self.assertEqual(q['parser'],'jq-backend')
                    self.assertNotIn('$j',q['root_selector'].replace('$$j',''))
                    self.assertEqual(q['columns'][0]['type'],'timestamp_epoch')

if __name__=='__main__':
    unittest.main()
