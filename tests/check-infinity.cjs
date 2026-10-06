// Test declarative JSONata queries against API fixtures; not a runtime collector.
const fs = require('fs');
const assert = require('assert/strict');
const jsonata = require('jsonata');
const queries = JSON.parse(fs.readFileSync('cicd-health/infinity-queries.json'));
const tc = {count: 1, build: [{id: 1, status: 'SUCCESS', startDate: '20261006T100000+0300', finishDate: '20261006T100100+0300'}]};
const oc = {TotalResults: 1, Items: [{State:'Success',StartTime:'2026-10-06T10:00:00+03:00',CompletedTime:'2026-10-06T10:02:00+03:00'}]};
let n = 0;
async function check(name,input,value) {
  const actual = await jsonata(queries[name].root_selector).evaluate(input);
  assert.equal(actual,value,name);n++;
}
(async()=>{
 await check('tc_status',tc,0);await check('tc_failed',tc,0);await check('tc_health',tc,3);await check('tc_duration',tc,60);
 await check('tc_status',{count:0,build:[]},-1);await check('tc_health',{count:0,build:[]},1);await check('tc_duration',{count:0,build:[]},-1);
 const failed=structuredClone(tc);failed.build[0].status='FAILURE';failed.build[0].canceledInfo=null;
 await check('tc_failed',failed,1);await check('tc_health',failed,2);
 failed.build[0].canceledInfo={text:'Canceled'};await check('tc_failed',failed,0);await check('tc_status',failed,3);
 await check('tc_running',{count:0,build:[]},0);await check('tc_agent',{count:1,agent:[{connected:true,authorized:true,enabled:true}]},1);
 await check('tc_agent',{count:0,agent:[]},0);
 await check('oc_status',oc,0);await check('oc_health',oc,3);await check('oc_failed',oc,0);await check('oc_duration',oc,120);
 for(const state of ['Failed','TimedOut']){const o=structuredClone(oc);o.Items[0].State=state;await check('oc_failed',o,1);await check('oc_health',o,2);}
 await check('oc_status',{TotalResults:0,Items:[]},-1);await check('oc_health',{TotalResults:0,Items:[]},1);
 await check('oc_queue',{TotalResults:241,Items:[{State:'Queued'}]},241);await check('oc_api',{TotalResults:0,Items:[]},1);
 await check('oc_running',{TotalResults:0,Items:[]},0);
 await assert.rejects(()=>jsonata(queries.oc_running.root_selector).evaluate({TotalResults:101,Items:[]}));n++;
 await assert.rejects(()=>jsonata(queries.tc_running.root_selector).evaluate({count:100,nextHref:'/next',build:[]}));n++;
 for (const name of Object.keys(queries)){await assert.rejects(()=>jsonata(queries[name].root_selector).evaluate({unexpected:true}),name);n++;}
 console.log(n+' JSONata fixture checks passed; '+Object.keys(queries).length+' queries');
})().catch(e=>{console.error(e);process.exit(1)});
