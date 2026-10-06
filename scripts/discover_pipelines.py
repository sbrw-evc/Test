"""Read-only API discovery and offline dashboard config preparation; not an exporter."""
from __future__ import annotations
import argparse, base64, json, os, re, ssl, subprocess, sys, tempfile
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit, quote
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
import yaml

class DiscoveryError(Exception): pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise DiscoveryError('API redirect refused; configure the final source URL')

class Client:
    def __init__(self, base, headers, ca_file=None, timeout=15):
        self.base=base.rstrip('/')+'/'
        u=urlsplit(self.base)
        if u.scheme!='https' or not u.hostname or u.username or u.password:
            raise DiscoveryError('Use an HTTPS source URL without embedded credentials')
        self.origin=(u.scheme,u.netloc)
        self.prefix=u.path.rstrip('/')+'/'
        self.headers={**headers,'Accept':'application/json'}
        context=ssl.create_default_context(cafile=ca_file or None)
        self.opener=build_opener(NoRedirect(),HTTPSHandler(context=context));self.timeout=timeout
    def get(self, path, params=None):
        url=urljoin(self.base,path)
        u=urlsplit(url)
        if (u.scheme,u.netloc)!=self.origin or not u.path.startswith(self.prefix):
            raise DiscoveryError('API pagination URL outside configured origin/context refused')
        if params:url+='?'+urlencode(params)
        try:
            with self.opener.open(Request(url,headers=self.headers,method='GET'),timeout=self.timeout) as r:
                data=r.read(16*1024*1024+1)
                if len(data)>16*1024*1024:raise DiscoveryError('API response too large')
                return json.loads(data)
        except HTTPError as e:raise DiscoveryError(f'API HTTP {e.code}; check permissions and source URL') from None
        except (URLError,ValueError) as e:raise DiscoveryError('API transport/JSON error; check DNS, TLS and response schema') from None

def require_env(name):
    value=os.environ.get(name)
    if not value:raise DiscoveryError('Missing credential environment variable '+name)
    return value

def paged_tc(client, locator, fields):
    path='app/rest/buildTypes';params={'locator':locator,'fields':fields};items=[];seen=set()
    for _ in range(10000):
        key=(path,json.dumps(params,sort_keys=True))
        if key in seen:raise DiscoveryError('TeamCity pagination loop')
        seen.add(key);page=client.get(path,params)
        if not isinstance(page,dict) or not isinstance(page.get('count'),int):raise DiscoveryError('Invalid TeamCity page')
        rows=page.get('buildType',[])
        if not isinstance(rows,list) or len(rows)!=page['count']:raise DiscoveryError('Incomplete TeamCity page')
        items.extend(rows);path=page.get('nextHref')
        if not path:return items
        params=None
    raise DiscoveryError('Too many TeamCity pages')

def paged_octopus(client,path):
    items=[];skip=0;total_expected=None
    for _ in range(10000):
        page=client.get(path,{'skip':skip,'take':200})
        if not isinstance(page,dict) or not isinstance(page.get('Items'),list) or not isinstance(page.get('TotalResults'),int):
            raise DiscoveryError('Invalid Octopus page')
        total=page['TotalResults']
        if total_expected is not None and total!=total_expected:raise DiscoveryError('Octopus inventory changed during pagination; retry')
        total_expected=total;rows=page['Items'];items.extend(rows);skip+=len(rows)
        if skip==total:return items
        if skip>total or not rows:raise DiscoveryError('Incomplete Octopus pagination')
    raise DiscoveryError('Too many Octopus pages')

def discover(config):
    options=config.get('discovery',{})
    tc=Client(config['teamcity_url'],{'Authorization':'Bearer '+require_env('TEAMCITY_TOKEN')},os.getenv('TEAMCITY_CA_FILE'))
    basic=base64.b64encode((require_env('JENKINS_USER')+':'+require_env('JENKINS_TOKEN')).encode()).decode()
    je=Client(config['jenkins_url'],{'Authorization':'Basic '+basic},os.getenv('JENKINS_CA_FILE'))
    oc=Client(config['octopus_url'],{'X-Octopus-ApiKey':require_env('OCTOPUS_API_KEY')},os.getenv('OCTOPUS_CA_FILE'))
    tcs=paged_tc(tc,options.get('teamcity_locator','defaultFilter:false,count:200'),
                 'count,nextHref,buildType(id,name,project(id,name),parameters(property(name,value)))')
    jobs=[];pending=[''];seen=set()
    while pending:
        full=pending.pop()
        if full in seen:raise DiscoveryError('Jenkins duplicate folder traversal')
        seen.add(full)
        if len(seen)>10000:raise DiscoveryError('Jenkins folder limit exceeded')
        path=''.join('job/'+quote(part,safe='')+'/' for part in full.split('/') if part)
        page=je.get(path+'api/json',{'tree':'jobs[name,fullName,_class,buildable]'})
        if not isinstance(page,dict) or not isinstance(page.get('jobs'),list):raise DiscoveryError('Invalid Jenkins jobs page')
        for j in page['jobs']:
            name=j.get('fullName') or (full+'/' if full else '')+j['name']
            clazz=j.get('_class','')
            if any(c in clazz for c in ('Folder','MultiBranchProject','OrganizationFolder')):pending.append(name)
            else:jobs.append({'fullName':name,'name':j['name']})
    spaces=options.get('octopus_spaces') or [config['octopus_space']]
    projects=[];envs=[]
    for space in spaces:
        projects.extend({**p,'SpaceId':space} for p in paged_octopus(oc,'api/'+quote(space,safe='')+'/projects'))
        envs.extend({**p,'SpaceId':space} for p in paged_octopus(oc,'api/'+quote(space,safe='')+'/environments'))
    # Never persist arbitrary build parameters: they can include unrelated secrets.
    safe_names=set(parameter_names(options).values())
    tcs=[{**{k:t.get(k) for k in ('id','name','project')},'parameters':{'property':[
        p for p in (t.get('parameters') or {}).get('property',[]) if p.get('name') in safe_names]}} for t in tcs]
    return {'teamcity':tcs,'jenkins':jobs,'octopus_projects':projects,'octopus_environments':envs}

def parameter_names(options):
    defaults={k:'monitoring.'+k for k in ('pipeline_id','pipeline_name','jenkins_job_regex','octopus_project','octopus_space','octopus_environment')}
    return {**defaults,**options.get('teamcity_parameters',{})}

def name_key(value,pattern):
    match=re.fullmatch(pattern,value or '')
    if not match:return None
    key=match.groupdict().get('pipeline') or match.group(0)
    return key.strip().casefold()

def single(rows):return rows[0] if len(rows)==1 else None

def resolve(config,inventory):
    o=config.get('discovery',{});pn=parameter_names(o)
    patterns={'teamcity':o.get('teamcity_name_regex',r'(?P<pipeline>.+)'),
              'jenkins':o.get('jenkins_name_regex',r'(?P<pipeline>[^/]+)(?:/.*)?'),
              'octopus':o.get('octopus_name_regex',r'(?P<pipeline>.+)')}
    for pat in patterns.values():re.compile(pat)
    for field in ('teamcity','jenkins','octopus_projects','octopus_environments'):
        if not isinstance(inventory.get(field),list):raise DiscoveryError('Inventory missing '+field)
    tcids=[t['id'] for t in inventory['teamcity']]
    if len(tcids)!=len(set(tcids)):raise DiscoveryError('Duplicate TeamCity buildType IDs')
    overrides=o.get('overrides',{})
    resolved=[];unresolved=[]
    for t in inventory['teamcity']:
        parameters={p['name']:p.get('value','') for p in (t.get('parameters') or {}).get('property',[])}
        meta={field:parameters.get(param) for field,param in pn.items()}
        meta.update(overrides.get(t['id'],{}))
        project=t.get('project') or {}
        source_name=(project.get('name') if o.get('teamcity_name_field','project')=='project' else t.get('name')) or t['name']
        key=name_key(source_name,patterns['teamcity']);errors=[];methods=[]
        space=meta.get('octopus_space') or config['octopus_space']
        environment=meta.get('octopus_environment') or config['octopus_environment']
        project_id=meta.get('octopus_project')
        if project_id:
            candidates=[p for p in inventory['octopus_projects'] if p['SpaceId']==space and p['Id']==project_id];methods.append('explicit Octopus ID')
        else:
            candidates=[p for p in inventory['octopus_projects'] if p['SpaceId']==space and key and name_key(p['Name'],patterns['octopus'])==key];methods.append('exact naming rule')
        octopus=single(candidates)
        if not octopus:errors.append('Octopus project missing or ambiguous')
        env=single([e for e in inventory['octopus_environments'] if e['SpaceId']==space and e['Id']==environment])
        if not env:errors.append('Configured Octopus environment missing or inaccessible')
        job_regex=meta.get('jenkins_job_regex')
        if job_regex:
            try:matching=[j for j in inventory['jenkins'] if re.fullmatch(job_regex,j['fullName'])]
            except re.error:errors.append('Invalid explicit Jenkins regex');matching=[]
            methods.append('explicit Jenkins regex')
        else:
            matching=[j for j in inventory['jenkins'] if key and name_key(j['fullName'],patterns['jenkins'])==key]
            job_regex='(?:'+'|'.join(re.escape(j['fullName']) for j in sorted(matching,key=lambda j:j['fullName']))+')'
        if not matching:errors.append('No Jenkins jobs match')
        pid=meta.get('pipeline_id') or ('tc-'+re.sub(r'[^A-Za-z0-9_-]','-',t['id']))
        name=meta.get('pipeline_name') or ((source_name+' / '+t['name']) if source_name!=t['name'] else t['name'])
        name=name.replace(',',' ').replace(':',' ')
        if not re.fullmatch(r'[A-Za-z0-9_-]+',pid):errors.append('Invalid pipeline_id')
        # Shared naming keys across different TC projects cannot prove a linkage.
        peers=[other for other in inventory['teamcity'] if key and name_key(((other.get('project') or {}).get('name') if o.get('teamcity_name_field','project')=='project' else other.get('name')) or other['name'],patterns['teamcity'])==key]
        project_ids={(p.get('project') or {}).get('id',p['id']) for p in peers}
        if len(project_ids)>1 and not (meta.get('octopus_project') and meta.get('jenkins_job_regex')):
            errors.append('Naming key shared by several TeamCity projects; explicit mapping required')
        if errors:
            unresolved.append({'teamcity_build_type':t['id'],'name':name,'key':key,'reasons':errors});continue
        resolved.append({'id':pid,'name':name,'teamcity_build_type':t['id'],'jenkins_job_regex':job_regex,
                         'octopus_space':space,'octopus_project':octopus['Id'],'octopus_environment':environment})
    if len({r['id'] for r in resolved})!=len(resolved):raise DiscoveryError('Duplicate resolved pipeline IDs')
    resolved.sort(key=lambda p:p['id'])
    catalog={'teamcity':[{'id':t['id'],'name':t['name'],'project':t.get('project')} for t in inventory['teamcity']],
             'jenkins':[j['fullName'] for j in inventory['jenkins']],
             'octopus_projects':[{k:p[k] for k in ('Id','Name','SpaceId')} for p in inventory['octopus_projects']],
             'octopus_environments':[{k:e[k] for k in ('Id','Name','SpaceId')} for e in inventory['octopus_environments']]}
    return {'resolved':resolved,'unresolved':unresolved,'catalog':catalog,
            'limitations':['Name matching requires a shared naming convention; IDs/overrides take precedence.',
                           'Environment is an explicit discovery scope, not inferred project lifecycle eligibility.',
                           'Pipeline discovery does not extend alert scopes.']}

def atomic_write(path,text):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.discovery-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:f.write(text)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='monitoring-config.yml')
    parser.add_argument('--inventory',help='Offline sanitized inventory JSON instead of API calls')
    parser.add_argument('--report',default='discovery-report.json')
    parser.add_argument('--output',help='Config output, default same as --config')
    parser.add_argument('--write',action='store_true',help='Replace pipelines in config after validation')
    parser.add_argument('--allow-partial',action='store_true',help='Explicitly allow unresolved sources; report remains required')
    parser.add_argument('--generate',action='store_true',help='Run offline dashboard generator after --write')
    args=parser.parse_args(argv)
    if args.generate and not args.write:parser.error('--generate requires --write')
    try:
        config=yaml.safe_load(Path(args.config).read_text())
        inventory=json.loads(Path(args.inventory).read_text()) if args.inventory else discover(config)
        report=resolve(config,inventory)
        atomic_write(args.report,json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(f"Resolved {len(report['resolved'])}; unresolved {len(report['unresolved'])}; report: {args.report}")
        if not report['resolved']:raise DiscoveryError('No complete pipelines resolved; config unchanged')
        if report['unresolved'] and not args.allow_partial:raise DiscoveryError('Unresolved mappings; config unchanged. Review report/rules/overrides')
        if args.write:
            config['pipelines']=report['resolved'];output=args.output or args.config
            text=yaml.safe_dump(config,sort_keys=False,allow_unicode=True)
            if not Path(output).exists() or Path(output).read_text()!=text:atomic_write(output,text)
            if args.generate:subprocess.run([sys.executable,str(Path(__file__).with_name('generate_monitoring.py')),output,'--dashboard-only'],check=True)
        return 0
    except (DiscoveryError,KeyError,TypeError,ValueError,OSError,yaml.YAMLError) as e:
        # No URLs, response bodies or credential values in transport exceptions.
        print('Discovery failed: '+str(e),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
