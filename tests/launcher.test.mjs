import {authenticatedFetch} from './auth-helper.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

test('background launcher survives parent exit and reopening reuses the running engine',async t=>{
  const root=fileURLToPath(new URL('..',import.meta.url));
  const temp=await mkdtemp(path.join(tmpdir(),'qe-launch-'));
  const runtime=path.join(temp,'runtime'),port=14321;
  const fetch=authenticatedFetch(path.join(temp,'data'));
  let serverPid=null;
  t.after(async()=>{
    if(serverPid){try{process.kill(serverPid);}catch(e){if(e.code!=='ESRCH')throw e;}}
    if(path.dirname(path.resolve(temp))!==path.resolve(tmpdir())||!path.basename(temp).startsWith('qe-launch-'))throw new Error('Unsafe test cleanup target');
    await rm(temp,{recursive:true,force:true,maxRetries:10,retryDelay:100});
  });
  const launch=()=>new Promise((resolve,reject)=>{
    const child=spawn(process.execPath,['tools/launch.mjs','--background','--no-open'],{cwd:root,env:{...process.env,PORT:String(port),QUANT_DATA_DIR:path.join(temp,'data'),QUANT_RUNTIME_DIR:runtime},stdio:'pipe',windowsHide:true});
    let output='';child.stdout.on('data',x=>output+=x);child.stderr.on('data',x=>output+=x);
    child.on('error',reject);child.on('exit',code=>resolve({code,output}));
  });
  const first=await launch();assert.equal(first.code,0,first.output);assert.match(first.output,/App ready:/);
  const record=JSON.parse(await readFile(path.join(runtime,`server-${port}.json`),'utf8'));serverPid=record.pid;
  const url=`http://127.0.0.1:${port}`;
  assert.equal((await fetch(url+'/api/health')).status,200);
  const second=await launch();assert.equal(second.code,0,second.output);assert.match(second.output,/already running/);
  const again=JSON.parse(await readFile(path.join(runtime,`server-${port}.json`),'utf8'));assert.equal(again.pid,serverPid);assert.equal(again.startedAt,record.startedAt);
  assert.equal((await fetch(url+'/api/portal')).status,200);
});
