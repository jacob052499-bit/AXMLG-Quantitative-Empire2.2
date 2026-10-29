import {authenticatedFetch} from './auth-helper.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {discoverPython} from '../lib/quantlab.mjs';
import {sampleRows} from '../lib/research-tools.mjs';
const root=fileURLToPath(new URL('..',import.meta.url));

test('Trading timezone, DST, factor causality, purged labels and independently calculated portfolio costs',async()=>{
  const env=await discoverPython(),r=spawnSync(env.command,['-m','unittest','discover','-s','python','-p','test_research_tools.py'],{cwd:root,encoding:'utf8',env:{...process.env,PYTHONIOENCODING:'utf-8'}});assert.equal(r.status,0,r.stdout+r.stderr);assert.match(r.stderr,/Ran 8 tests/);
});
test('Visual sampling preserves terminal NAV and extreme spikes without changing full input',()=>{
  const rows=Array.from({length:5000},(_,i)=>({date:String(i),nav:i===1234?-10000:i===4321?10000:i}));const out=sampleRows(rows,100);assert.ok(out.length<=100);assert.equal(out[0].date,'0');assert.equal(out.at(-1).date,'4999');assert.ok(out.some(x=>x.nav===-10000));assert.ok(out.some(x=>x.nav===10000));assert.equal(rows.length,5000);
  rows.forEach((r,i)=>r.quantity=i===1235?9999:0);const multi=sampleRows(rows,100,['nav','quantity']);assert.ok(multi.length<=100);assert.ok(multi.some(x=>x.quantity===9999));assert.ok(multi.some(x=>x.nav===-10000));
});
test('Factor API: single / portfolio jobs, history, report, ledgers, compact views and exact bundle replay',async t=>{
  const folder=await mkdtemp(path.join(tmpdir(),'qe-factor-api-')),port=14325;const fetch=authenticatedFetch(folder);
  const child=spawn(process.execPath,['tests/start-research-server.mjs'],{cwd:root,windowsHide:true,env:{...process.env,PORT:String(port),QUANT_DATA_DIR:folder},stdio:'pipe'});let output='';child.stdout.on('data',x=>output+=x);child.stderr.on('data',x=>output+=x);
  t.after(async()=>{child.kill();await new Promise(r=>child.exitCode!==null?r():child.once('exit',r));if(path.dirname(path.resolve(folder))!==path.resolve(tmpdir())||!path.basename(folder).startsWith('qe-factor-api-'))throw Error('Unsafe cleanup');await rm(folder,{recursive:true,force:true,maxRetries:10,retryDelay:100});});
  const base='http://127.0.0.1:'+port;for(let i=0;i<120;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}await new Promise(r=>setTimeout(r,50));}
  const req=async(p,b)=>{const r=await fetch(base+p,b?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}:{});const v=await r.json();assert.ok(r.ok,JSON.stringify(v)+output);return v;};
  const wait=async id=>{for(let i=0;i<240;i++){const j=await req('/api/python/jobs/'+id);if(!['RUNNING','QUEUED'].includes(j.status)){assert.equal(j.status,'COMPLETED',j.console+'\n'+j.error);return j;}await new Promise(r=>setTimeout(r,50));}throw Error('Job timeout');};
  const boot=await req('/api/bootstrap');let job=await req('/api/python/jobs',{action:'factor',datasetIds:[boot.datasets[0].id],name:'Factor single fixture',config:{horizon:5},tools:{mode:'single'}});job=await wait(job.id);
  let record=await req('/api/factors/'+job.factorId);assert.ok(record.selected.length);assert.equal(record.causality.status,'PASS_PREFIX');assert.equal((await req('/api/factors')).length,1);
  const selected=[];
  for(let asset=0;asset<3;asset++){
    const rows=['timestamp,open,high,low,close,volume'];for(let i=0;i<500;i++){const p=100+Math.sin(i/(11+asset*7)+asset)*10+i*(.01+asset*.02);rows.push(`${new Date(Date.UTC(2025,0,1)+i*3600000).toISOString()},${p},${p*1.01},${p*.99},${p*(1+.002*Math.sin(i+asset))},${100+i%23}`);}
    selected.push((await req('/api/datasets',{csv:rows.join('\n'),metadata:{symbol:'SYNTH-FACTOR-'+asset,source:'Synthetic engineering fixture only',timeframe:'1 Hour',synthetic:true}})).id);
  }
  job=await req('/api/python/jobs',{action:'factor',datasetIds:selected,name:'Factor portfolio fixture',config:{horizon:5,topAssets:2,rebalanceBars:12,tradingTimezone:'America/New_York'},tools:{mode:'portfolio'}});job=await wait(job.id);record=await req('/api/factors/'+job.factorId);assert.equal(record.quality.synthetic,true);assert.equal(record.mode,'portfolio');assert.ok(record.evaluation.fills.length);assert.ok(record.evaluation.nav.every(x=>x.cash>=0));
  for(const type of ['report','nav','fills','scores']){const response=await fetch(base+'/api/factors/'+job.factorId+'/'+type);assert.ok(response.ok);assert.ok((await response.text()).length>100);}
  const bundle=await req('/api/factors/'+job.factorId+'/bundle'),file=path.join(folder,'bundle.json');await writeFile(file,JSON.stringify(bundle));const replay=spawnSync(process.execPath,['tools/replay-factors.mjs',file],{cwd:root,encoding:'utf8',windowsHide:true});assert.equal(replay.status,0,replay.stdout+replay.stderr);assert.match(replay.stdout,/PASS:/);
  const ui=await req('/api/runs/'+boot.runs[0].id+'?view=ui');assert.equal(ui.uiView,true);assert.equal(ui.evaluation.decisions.length,0);assert.deepEqual(ui.evaluation.metrics,boot.runs[0].metrics);
  assert.equal((await fetch(base+'/research.js')).status,200);
});
