import {readFile,writeFile,mkdtemp,rm} from 'node:fs/promises';import path from 'node:path';import {tmpdir} from 'node:os';import {spawnSync} from 'node:child_process';import {hash} from '../lib/data.mjs';
let folder;
try{
 const b=JSON.parse(await readFile(process.argv[2],'utf8'));if(b.format!=='QE_BATCH_BUNDLE')throw Error('Expected QE_BATCH_BUNDLE');const {hash:signature,...contract}=b.record.contract;if(hash(contract)!==signature)throw Error('Batch contract hash mismatch');
 b.datasets.forEach((d,i)=>{if(d.id!==contract.datasetIds[i]||hash({identity:d.identity,bars:d.bars})!==contract.datasetHashes[i])throw Error('Dataset fingerprint mismatch');});
 folder=await mkdtemp(path.join(tmpdir(),'qe-batch-replay-'));let completed=0;
 for(const item of b.record.items){if(!item.runId)continue;const run=b.runs.find(r=>r.id===item.runId),dataset=b.datasets.find(d=>d.id===item.datasetId);if(!run||!dataset||run.contract.programHash!==contract.programHash||run.contract.python.sourceHash!==contract.sourceHash)throw Error('Missing or mismatched child');const file=path.join(folder,item.runId+'.json');await writeFile(file,JSON.stringify({format:'QUANTITATIVE_EMPIRE_RESEARCH_BUNDLE',run,dataset}));const p=spawnSync(process.execPath,['tools/replay.mjs',file],{encoding:'utf8',windowsHide:true,timeout:360000,maxBuffer:4*1024*1024});if(p.status!==0)throw Error(p.stdout+'\n'+p.stderr);console.log(p.stdout.trim());completed++;}
 console.log(`PASS: batch ${b.record.id}; ${completed} completed independent accounts replayed. Excluded/uncompleted items retained, not treated as simulated portfolios or independently replayed successes.`);
}catch(e){console.error('FAIL: '+e.message);process.exitCode=1;}
finally{if(folder){if(path.dirname(folder)!==tmpdir()||!path.basename(folder).startsWith('qe-batch-replay-'))throw Error('Unsafe cleanup');await rm(folder,{recursive:true,force:true,maxRetries:10,retryDelay:100});}}
