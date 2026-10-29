import {readFile,writeFile,mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
import {spawnSync} from 'node:child_process';
import {hash} from '../lib/data.mjs';
import {SOURCE_FILES} from '../lib/source-files.mjs';
import {discoverPython} from '../lib/quantlab.mjs';
const root=fileURLToPath(new URL('..',import.meta.url));let folder;
try{
  if(!process.argv[2])throw Error('Usage: node tools/replay-factors.mjs <factor-bundle.json>');
  const bundle=JSON.parse(await readFile(process.argv[2],'utf8'));if(bundle.format!=='QE_FACTOR_BUNDLE')throw Error('Unknown bundle format');
  const {record:r,datasets}=bundle,{hash:signature,...c}=r.contract;if(hash(c)!==signature)throw Error('Contract hash mismatch');
  if(datasets.length!==c.datasetHashes.length)throw Error('Dataset count mismatch');
  datasets.forEach((d,i)=>{if(d.id!==c.datasetIds[i]||hash({identity:d.identity,bars:d.bars})!==c.datasetHashes[i])throw Error('Dataset hash mismatch');});
  let sourceRoot;
  for(const candidate of [path.join(root,'data','source-archive',c.programHash),root]){
    try{let files=SOURCE_FILES;try{files=JSON.parse(await readFile(path.join(candidate,'manifest.json'),'utf8')).sourceFiles;}catch{}
      if(hash((await Promise.all(files.map(f=>readFile(path.join(candidate,f),'utf8')))).join('\n'))===c.programHash){sourceRoot=candidate;break;}
    }catch{}
  }
  if(!sourceRoot)throw Error('Exact program snapshot unavailable. Keep data/source-archive with the bundle.');
  const env=await discoverPython();if(!env.available)throw Error(env.error);for(const k of ['python','numpy','pandas'])if(env[k]!==c.environment[k])throw Error(k+' version mismatch');
  folder=await mkdtemp(path.join(tmpdir(),'qe-factor-replay-'));await writeFile(path.join(folder,'input.json'),JSON.stringify({action:'factor',datasets,config:c.config,tools:c.tools}));
  const child=spawnSync(env.command,['-u',path.join(sourceRoot,'python','worker.py'),path.join(folder,'input.json'),path.join(folder,'result.json')],{windowsHide:true,encoding:'utf8',timeout:300000,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
  if(child.status!==0)throw Error(child.error?.message||child.stderr||'Worker failed');
  const actual=JSON.parse(await readFile(path.join(folder,'result.json'),'utf8'));
  for(const k of Object.keys(actual))if(JSON.stringify(actual[k])!==JSON.stringify(r[k]))throw Error(k+' mismatch');
  console.log('PASS: '+r.id+'\nData, contract and program hashes verified; IC, selection, costs, weights and NAV reproduced exactly.');
}catch(e){console.error('FAIL: '+e.message);process.exitCode=1;}
finally{if(folder){if(path.dirname(path.resolve(folder))!==path.resolve(tmpdir())||!path.basename(folder).startsWith('qe-factor-replay-'))throw Error('Unsafe cleanup');await rm(folder,{recursive:true,force:true,maxRetries:10,retryDelay:100});}}
