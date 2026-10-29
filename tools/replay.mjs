import { readFile,writeFile,mkdtemp,rm } from 'node:fs/promises';
import { fileURLToPath,pathToFileURL } from 'node:url';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';
import { hash, validateBars } from '../lib/data.mjs';
import { discoverPython } from '../lib/quantlab.mjs';
import { SOURCE_FILES } from '../lib/source-files.mjs';

const file=process.argv[2];
const root=fileURLToPath(new URL('..',import.meta.url));
let temp;
if(!file){console.error('Usage: node tools/replay.mjs <run-bundle.json>');process.exit(1);}
try{
  const bundle=JSON.parse(await readFile(file,'utf8'));
  if(bundle.format!=='QUANTITATIVE_EMPIRE_RESEARCH_BUNDLE')throw new Error('Unknown bundle format');
  const {run,dataset}=bundle;
  if(hash({identity:dataset.identity,bars:dataset.bars})!==dataset.fingerprint)throw new Error('Dataset fingerprint mismatch');
  const {hash:contractHash,...contract}=run.contract;
  if(hash(contract)!==contractHash)throw new Error('Contract fingerprint mismatch');
  let sourceRoot=null;
  for(const candidate of [path.join(root,'data','source-archive',contract.programHash),path.join(root,'data','source-archive','v0.1.0'),root]){
    try{let files;try{files=JSON.parse(await readFile(path.join(candidate,'manifest.json'),'utf8')).sourceFiles;}catch{files=candidate.endsWith('v0.1.0')?['lib/engine.mjs','lib/data.mjs','server.mjs']:SOURCE_FILES;}
      if(hash((await Promise.all(files.map(f=>readFile(path.join(candidate,f),'utf8')))).join('\n'))===contract.programHash){sourceRoot=candidate;break;}
    }catch{}
  }
  if(!sourceRoot)throw new Error('Original program snapshot unavailable or fingerprint mismatch. Keep data/source-archive with the bundle.');
  let result;
  if(contract.engine==='python'){
    const environment=await discoverPython();if(!environment.available)throw new Error(environment.error);
    for(const name of ['python','numpy','pandas'])if(environment[name]!==contract.python.environment[name])throw new Error(`${name} version mismatch`);
    temp=await mkdtemp(path.join(tmpdir(),'qe-replay-'));const input=path.join(temp,'input.json'),output=path.join(temp,'result.json');
    await writeFile(input,JSON.stringify({dataset,source:contract.python.source,params:contract.python.params,grid:contract.python.grid,tools:contract.python.tools,config:contract.config,action:'research'}));
    const child=spawnSync(environment.command,['-u',path.join(sourceRoot,'python','worker.py'),input,output],{cwd:temp,windowsHide:true,encoding:'utf8',timeout:300000,maxBuffer:2*1024*1024,env:{...process.env,PYTHONIOENCODING:'utf-8',OMP_NUM_THREADS:'1',OPENBLAS_NUM_THREADS:'1'}});
    if(child.status!==0)throw new Error(child.error?.message||child.stderr||'Python replay failed');
    result=JSON.parse(await readFile(output,'utf8'));
    if(JSON.stringify(result.parameters)!==JSON.stringify(run.parameters))throw new Error('Selected parameters mismatch');
    if(JSON.stringify(result.plateau)!==JSON.stringify(run.analysis.plateau))throw new Error('Plateau mismatch');
    if(JSON.stringify(result.monteCarlo||null)!==JSON.stringify(run.analysis.monteCarlo))throw new Error('Monte Carlo mismatch');
    for(const key of ['walkForward','causality','statistics','stressTests'])if(JSON.stringify(result[key]||null)!==JSON.stringify(run.analysis[key]||null))throw new Error(`${key} mismatch`);
    if(JSON.stringify(result.full||null)!==JSON.stringify(run.full||null))throw new Error('Full-period event simulation mismatch');
    if(run.dataAudit&&JSON.stringify(result.dataAudit)!==JSON.stringify(run.dataAudit))throw new Error('Data audit mismatch');
    if(run.evaluation.executionAudit&&JSON.stringify(result.evaluation.executionAudit)!==JSON.stringify(run.evaluation.executionAudit))throw new Error('Execution audit mismatch');
  }else{
    const {runResearch}=await import(pathToFileURL(path.join(sourceRoot,'lib','engine.mjs')).href);
    const quality=validateBars(dataset.bars,dataset.identity.calendar);result=runResearch({...dataset,quality},contract.config,{id:run.id,programHash:contract.programHash});
    if(JSON.stringify(result.selected)!==JSON.stringify(run.selected))throw new Error('Selected parameters mismatch');
  }
  for(const section of ['development','evaluation','baseline','stress']){
    if(JSON.stringify(result[section].metrics)!==JSON.stringify(run[section].metrics))throw new Error(`${section} metrics mismatch`);
  }
  if(JSON.stringify(result.evaluation.fills)!==JSON.stringify(run.evaluation.fills))throw new Error('Fill ledger mismatch');
  if(JSON.stringify(result.evaluation.nav)!==JSON.stringify(run.evaluation.nav))throw new Error('Daily NAV mismatch');
  console.log(`PASS: ${run.id}\nDataset / contract / program hashes verified.\nDevelopment, evaluation, baseline, stress, selected parameters, fills and NAV reproduced exactly.`);
}catch(e){console.error(`FAIL: ${e.message}`);process.exitCode=1;}
finally{if(temp){if(path.dirname(path.resolve(temp))!==path.resolve(tmpdir())||!path.basename(temp).startsWith('qe-replay-'))throw Error('Unsafe cleanup');await rm(temp,{recursive:true,force:true,maxRetries:10,retryDelay:100});}}
