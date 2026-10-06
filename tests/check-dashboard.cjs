// Verify filters and linked CI/CD scopes without a live Grafana instance.
const fs=require('fs'), assert=require('assert/strict'), jsonata=require('jsonata');
const d=JSON.parse(fs.readFileSync('cicd-health/cicd-health-dashboard.json'));
const vars=d.templating.list, byname=Object.fromEntries(vars.map(v=>[v.name,v]));
const scalar={pipeline:'ui-test',tc_build_type:'TC_UI_Test',octopus_space:'Spaces-88',octopus_project:'Projects-77',octopus_environment:'Environments-66',pipeline_jenkins_jobs:'ui/.*',jenkins_pool:'linux',jenkins_job:['ui/test.one','ui/test-two'],jenkins_node:['linux-01','linux-02'],component:['teamcity','octopus']};
const regescape=s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
const regex=v=>Array.isArray(v)?'('+v.map(regescape).join('|')+')':regescape(v);
function interpolate(s){return s.replace(/\$\{(\w+)(?::(\w+))?\}/g,(_,name,format)=>{
 const val=scalar[name];assert.notEqual(val,undefined,'unbound '+name);
 return format==='percentencode'?encodeURIComponent(val):format==='json'?JSON.stringify(val):format==='regex'?regex(val):val;
});}
(async()=>{
 assert.equal(d.panels.filter(p=>p.type==='row').length,7);
 assert.equal(d.panels.filter(p=>p.type!=='row').length,26);
 assert.equal(new Set(d.panels.map(p=>p.id)).size,d.panels.length);
 for(const [idx,p] of d.panels.entries())for(const p2 of d.panels.slice(idx+1)){
  const a=p.gridPos,b=p2.gridPos;
  assert(!(a.x<b.x+b.w&&a.x+a.w>b.x&&a.y<b.y+b.h&&a.y+a.h>b.y),`overlap ${p.id},${p2.id}`);
 }
 const first=d.panels.find(p=>p.id===1).options.content;
 assert(first.includes('${pipeline:text}'));
 for(const name of ['tc_build_type','pipeline_jenkins_jobs','octopus_space','octopus_project','octopus_environment']){
  const v=byname[name],q=v.query.infinityQuery;
  assert.equal(v.hide,2);assert.equal(q.source,'inline');
  const defs=JSON.parse(q.data), test={...defs[0],id:'ui-test',teamcity_build_type:scalar.tc_build_type,jenkins_job_regex:scalar.pipeline_jenkins_jobs,jenkins_job_regex_promql:scalar.pipeline_jenkins_jobs,octopus_space:scalar.octopus_space,octopus_project:scalar.octopus_project,octopus_environment:scalar.octopus_environment};
  const result=await jsonata(interpolate(q.root_selector)).evaluate([...defs,test]);
  assert.equal(result.length,1);assert.equal(result[0].__value,scalar[name]);
 }
 assert.equal(byname.jenkins_job.multi,true);assert.equal(byname.jenkins_pool.multi,false);
 assert(byname.jenkins_job.query.query.includes('${pipeline_jenkins_jobs:raw}'));
 for(const p of d.panels)for(const t of p.targets||[]){
  if(t.url){const u=new URL(interpolate(t.url));
   if(u.pathname.includes('/builds'))assert(u.searchParams.get('locator').includes('id:TC_UI_Test'));
   if(u.pathname.includes('/tasks')){assert(u.pathname.includes('/api/Spaces-88/'));assert.equal(u.searchParams.get('project'),'Projects-77');assert.equal(u.searchParams.get('environment'),'Environments-66');}
  }
  if(t.expr)assert(!interpolate(t.expr).includes('${'));
 }
 for(const pid of [2,3,4]){const link=d.panels.find(p=>p.id===pid).links.find(l=>l.title==='Детали в Grafana');assert(link.includeVars&&link.keepTime);}
 const alerts=fs.readFileSync('cicd-health/grafana-alert-rules.yml','utf8');assert(!alerts.includes('${pipeline'));assert(!alerts.includes('${jenkins_'));assert(!alerts.includes('${octopus_'));
 console.log('PASS: 7 rows, stable panel IDs, no overlaps, linked pipeline scope, URL interpolation, filter links, fixed alerts');
})().catch(e=>{console.error(e);process.exit(1)});
