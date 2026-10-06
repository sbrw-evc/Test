"""Validate discovery-generated dashboard structure before proposing an update."""
import json,re
from pathlib import Path

def validate(path):
    d=json.loads(Path(path).read_text())
    v={v['name']:v for v in d['templating']['list']}
    model=v['tc_build_type']['query']['infinityQuery']
    pipelines=json.loads(model['data'])
    assert pipelines,'Empty pipeline catalog'
    assert len({p['id'] for p in pipelines})==len(pipelines),'Duplicate pipeline IDs'
    for p in pipelines:
        assert re.fullmatch(r'[A-Za-z0-9_-]+',p['id'])
        assert all(p.get(k) for k in ('name','teamcity_build_type','jenkins_job_regex','octopus_space','octopus_project','octopus_environment'))
        assert p['jenkins_job_regex_promql']==json.dumps(p['jenkins_job_regex'])[1:-1]
    assert len([p for p in d['panels'] if p['type']=='row'])==7
    assert len({p['id'] for p in d['panels']})==len(d['panels'])
    assert not any('${pipeline' in p.get('url','') for p in d.get('links',[]))
    print(f'Validated {len(pipelines)} pipeline mappings and dashboard structure')
if __name__=='__main__':validate(Path(__file__).resolve().parents[1]/'cicd-health/cicd-health-dashboard.json')
