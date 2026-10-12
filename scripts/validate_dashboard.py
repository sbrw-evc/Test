"""Validate the current Grafana v2 dashboard structure."""
import json
from pathlib import Path

def validate(path):
 d=json.loads(Path(path).read_text());s=d['spec']
 assert d['apiVersion']=='dashboard.grafana.app/v2'
 assert len(s['layout']['spec']['rows'])==5
 assert all(v['spec']['name']!='display_timezone' for v in s['variables'])
 assert not s['links']
 def walk(layout):
  if layout['kind']=='RowsLayout':
   for r in layout['spec']['rows']:walk(r['spec']['layout'])
  else:
   for i in layout['spec']['items']:assert i['spec']['element']['name'] in s['elements']
 walk(s['layout']);print('Validated',len(s['elements']),'panels, 5 rows, v2 layout references')
if __name__=='__main__':validate(Path(__file__).resolve().parents[1]/'cicd-health/cicd-health-dashboard.json')
