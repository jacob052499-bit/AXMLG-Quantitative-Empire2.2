import {readFile,writeFile,mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';import {fileURLToPath} from 'node:url';import {tmpdir} from 'node:os';import {spawnSync} from 'node:child_process';
import {hash} from '../lib/data.mjs';import {SOURCE_FILES} from '../lib/source-files.mjs';import {discoverPython} from '../lib/quantlab.mjs';
const root=fileURLToPath(new URL('..',import.meta.url));let folder;
try{
 if(!process.argv[2])throw Error('Usage: node tools/replay-scans.mjs <scan-bundle.json>');
 const b=JSON.parse(await readFile(process.argv[2],'utf8'));if(b.format!=='QE_SCAN_BUNDLE')throw Error('Unknown scan bundle');
 const r=b.record,{hash:signature,...c}=r.contract;if(hash(c)!==signature)throw Error('Contract hash mismatch');
 if(!['COMPLETED','COMPLETED_WITH_EXCLUSIONS'].includes(r.status))throw Error('完整批次重播只適用已完成掃描；部分紀錄請個別重播已完成的因子研究包。');
 if(b.datasets.length!==c.datasetHashes.length)throw Error('Dataset count mismatch');
 b.datasets.forEach((d,i)=>{if(d.id!==c.datasetIds[i]||hash({identity:d.identity,bars:d.bars})!==c.datasetHashes[i])throw Error('Dataset hash mismatch');});
 let source;
 for(const candidate of [path.join(root,'data','source-archive',c.programHash),root]){try{let files=SOURCE_FILES;try{files=JSON.parse(await readFile(path.join(candidate,'manifest.json'),'utf8')).sourceFiles;}catch{}if(hash((await Promise.all(files.map(f=>readFile(path.join(candidate,f),'utf8')))).join('\n'))===c.programHash){source=candidate;break;}}catch{}}
 if(!source)throw Error('Exact program snapshot unavailable');const env=await discoverPython();if(!env.available)throw Error(env.error);for(const k of ['python','numpy','pandas'])if(env[k]!==c.environment[k])throw Error(k+' version mismatch');
 folder=await mkdtemp(path.join(tmpdir(),'qe-scan-replay-'));await writeFile(path.join(folder,'input.json'),JSON.stringify({action:'scan',datasets:b.datasets,config:c.config,tools:c.tools}));
 const p=spawnSync(env.command,['-u',path.join(source,'python','worker.py'),path.join(folder,'input.json'),path.join(folder,'output.json')],{cwd:folder,windowsHide:true,encoding:'utf8',timeout:3600000,env:{...process.env,PYTHONIOENCODING:'utf-8'}});if(p.status!==0)throw Error(p.error?.message||p.stderr||'Worker failed');
 const actual=JSON.parse(await readFile(path.join(folder,'output.json'),'utf8')),expected=structuredClone(r);expected.items.forEach(x=>delete x.factorId);
 for(const k of Object.keys(actual))if(JSON.stringify(actual[k])!==JSON.stringify(expected[k]))throw Error(k+' mismatch');
 for(const item of actual.items.filter(x=>x.status==='COMPLETED')){const raw=JSON.parse(await readFile(path.join(folder,item.recordFile),'utf8')),child=b.factors.find(x=>x.id===r.items.find(x=>x.datasetId===item.datasetId).factorId);if(!child)throw Error('Missing child factor record');for(const k of Object.keys(raw))if(JSON.stringify(raw[k])!==JSON.stringify(child[k]))throw Error(item.datasetId+': '+k+' mismatch');const {hash:sig,...contract}=child.contract;if(hash(contract)!==sig)throw Error('Child contract mismatch');}
 console.log('PASS: '+r.id+'\nData, program and contract hashes verified. All per-asset studies, batch BY, validation-only scores, recommendations and exclusions reproduced exactly.');
}catch(e){console.error('FAIL: '+e.message);process.exitCode=1;}
finally{if(folder){if(path.dirname(path.resolve(folder))!==path.resolve(tmpdir())||!path.basename(folder).startsWith('qe-scan-replay-'))throw Error('Unsafe cleanup');await rm(folder,{recursive:true,force:true,maxRetries:10,retryDelay:100});}}
