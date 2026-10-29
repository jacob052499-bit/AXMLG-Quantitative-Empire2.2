import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
const {ensureEngine,navigationKind}=createRequire(import.meta.url)('../desktop/engine.cjs');
const {fitBounds,validPage,safeDownloadName}=createRequire(import.meta.url)('../desktop/window-state.cjs');

test('desktop window recovery fits removed monitors and small screens without losing the window',()=>{
 const area={x:0,y:0,width:1280,height:720},fitted=fitBounds({x:4000,y:-2000,width:2000,height:1200},area);assert.deepEqual(fitted,{x:0,y:0,width:1280,height:720,minWidth:960,minHeight:640});
 const small=fitBounds(null,{x:-800,y:0,width:800,height:600});assert.ok(small.x>=-800&&small.x+small.width<=0);assert.equal(small.minWidth,800);assert.equal(small.minHeight,600);
 assert.equal(validPage('javascript:alert(1)'),'overview');assert.equal(validPage('factors'),'factors');assert.equal(safeDownloadName('../../report:2026?.html'),'report_2026_.html');
});

test('desktop links allow only the local engine or explicit HTTP web links',()=>{
 const base='http://127.0.0.1:4310';
 assert.equal(navigationKind(base+'/#python',base),'local');assert.equal(navigationKind(base+'/user-guide.html',base),'local');
 assert.equal(navigationKind('https://www.electronjs.org/docs/',base),'external');
 for(const url of ['file:///C:/secret','javascript:alert(1)','data:text/html,hi','http://localhost:4000','http://127.0.0.1:22','not a url'])assert.equal(navigationKind(url,base),'blocked',url);
 assert.notEqual(navigationKind('http://127.0.0.1:4310@evil.invalid/',base),'local');
});

test('desktop starts an engine without a terminal, reuses its PID and rejects another project',async t=>{
 const root=await mkdtemp(path.join(tmpdir(),'qe-desktop-')),runtime=path.join(root,'runtime'),port=14324;let pid;
 t.after(async()=>{if(pid){try{process.kill(pid);}catch(e){if(e.code!=='ESRCH')throw e;}}if(path.dirname(path.resolve(root))!==path.resolve(tmpdir())||!path.basename(root).startsWith('qe-desktop-'))throw Error('Unsafe cleanup');await rm(root,{recursive:true,force:true,maxRetries:10,retryDelay:100});});
 await writeFile(path.join(root,'server.mjs'),`import http from 'node:http';http.createServer((req,res)=>{res.setHeader('content-type','application/json');res.end(JSON.stringify({ok:true,version:'test',programHash:'a'.repeat(64)}));}).listen(Number(process.env.PORT),'127.0.0.1');`);
 const [first,concurrent]=await Promise.all([ensureEngine({root,runtime,port,timeout:8000}),ensureEngine({root,runtime,port,timeout:8000})]);pid=first.pid;assert.equal(first.reused,false);assert.ok(pid>0);assert.equal(concurrent.pid,pid);assert.equal(concurrent.reused,true);
 const second=await ensureEngine({root,runtime,port});assert.equal(second.reused,true);assert.equal(second.pid,pid);
 const file=path.join(runtime,`server-${port}.json`),record=JSON.parse(await readFile(file,'utf8'));assert.equal(record.launcher,'desktop');
 await writeFile(file,JSON.stringify({...record,root:tmpdir()}));await assert.rejects(ensureEngine({root,runtime,port}),/另一個研究專案/);
 assert.equal((await fetch(first.url+'/api/health')).status,200);
});

test('slow desktop boot preserves its process record so retry reuses the same engine',async t=>{
 const root=await mkdtemp(path.join(tmpdir(),'qe-desktop-')),runtime=path.join(root,'runtime'),port=14326;let pid;
 t.after(async()=>{if(pid){try{process.kill(pid);}catch(e){if(e.code!=='ESRCH')throw e;}}if(path.dirname(path.resolve(root))!==path.resolve(tmpdir())||!path.basename(root).startsWith('qe-desktop-'))throw Error('Unsafe cleanup');await rm(root,{recursive:true,force:true,maxRetries:10,retryDelay:100});});
 await writeFile(path.join(root,'server.mjs'),`import http from 'node:http';setTimeout(()=>http.createServer((req,res)=>res.end(JSON.stringify({ok:true,version:'slow',programHash:'b'.repeat(64)}))).listen(Number(process.env.PORT),'127.0.0.1'),700);`);
 await assert.rejects(ensureEngine({root,runtime,port,timeout:80}),/保留啟動紀錄/);const file=path.join(runtime,`server-${port}.json`);pid=JSON.parse(await readFile(file,'utf8')).pid;assert.ok(pid>0);
 const recovered=await ensureEngine({root,runtime,port,timeout:8000});assert.equal(recovered.pid,pid);assert.equal(recovered.reused,true);assert.equal(JSON.parse(await readFile(file,'utf8')).status,'READY');
});
