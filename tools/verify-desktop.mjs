// Verify the bundled engine and Python in a disposable data directory.
import {createRequire} from 'node:module';
import {readFile,writeFile,mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const {ensureEngine}=createRequire(import.meta.url)('../desktop/engine.cjs');
const root=fileURLToPath(new URL('..',import.meta.url)),install=JSON.parse(await readFile(path.join(root,'.desktop','installation.json'),'utf8'));
const temp=await mkdtemp(path.join(tmpdir(),'qe-desktop-cold-'));let pid;
try{
 const engine=await ensureEngine({root,runtime:path.join(temp,'runtime'),port:14325,executable:install.executable,env:{...process.env,ELECTRON_RUN_AS_NODE:'1',QUANT_DATA_DIR:path.join(temp,'data')}});if(engine.reused)throw Error('Cold test unexpectedly reused an engine');pid=engine.pid;
 const get=async p=>{const r=await fetch(engine.url+p);if(!r.ok)throw Error(await r.text());return r.json();};
 const environment=await get('/api/python/environment');if(!environment.available)throw Error(environment.error);
 const boot=await get('/api/bootstrap'),template=(await get('/api/python/templates')).find(t=>t.id==='sma');
 const created=await fetch(engine.url+'/api/python/jobs',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({datasetId:boot.datasets[0].id,action:'research',name:'Desktop cold-start verification · synthetic only',source:template.source,params:template.params,grid:{},config:{split:.65},tools:{quick:true}})});if(!created.ok)throw Error(await created.text());let job=await created.json();
 const deadline=Date.now()+60000;
 while(['QUEUED','RUNNING'].includes(job.status)&&Date.now()<deadline){await new Promise(r=>setTimeout(r,250));job=await get('/api/python/jobs/'+job.id);}
 if(job.status!=='COMPLETED')throw Error('Bundled desktop backtest failed: '+JSON.stringify({status:job.status,error:job.error,console:job.console}));
 const run=await get('/api/runs/'+job.runId);
 if(run.execution_state!=='COMPLETED'||!Number.isFinite(run.evaluation.metrics.netPnl))throw Error('No complete backtest ledger');
 const again=await ensureEngine({root,runtime:path.join(temp,'runtime'),port:14325});if(!again.reused||again.pid!==pid)throw Error('Desktop reopened a duplicate engine');
 const proof={passed:true,desktopVersion:JSON.parse(await readFile(path.join(root,'desktop','package.json'),'utf8')).version,runtimeVersion:install.runtimeVersion,coldEngineStarted:true,bundledRuntimeReused:true,python:environment.python,numpy:environment.numpy,pandas:environment.pandas,syntheticBacktestStatus:job.status,tradeCount:run.evaluation.metrics.tradeCount,testDataIsolated:true,verifiedAt:new Date().toISOString()};
 await writeFile(path.join(root,'.runtime','desktop-verification.json'),JSON.stringify(proof,null,2));console.log(JSON.stringify(proof,null,2));
}finally{
 if(pid){try{process.kill(pid);}catch(e){if(e.code!=='ESRCH')throw e;}}
 if(path.dirname(path.resolve(temp))!==path.resolve(tmpdir())||!path.basename(temp).startsWith('qe-desktop-cold-'))throw Error('Unsafe temporary cleanup target');
 await rm(temp,{recursive:true,force:true,maxRetries:10,retryDelay:100});
}
