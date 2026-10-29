import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../app/client-core.js',import.meta.url),'utf8');
const scope=vm.createContext({fetch,Headers,AbortController,setTimeout,clearTimeout,module:{exports:{}}});vm.runInContext(source,scope);const core=scope.module.exports;
test('client preserves validation/conflict messages and treats invalid engine replies as disconnected',async()=>{
 const connections=[];const conflict=core.createClient({fetcher:async()=>new Response(JSON.stringify({error:'筆記已被另一個視窗修改'}),{status:409}),onConnection:x=>connections.push(x)});
 await assert.rejects(conflict('/note',{method:'POST',body:'{}'}),e=>e.status===409&&e.message.includes('另一個視窗'));assert.deepEqual(connections,[true]);
 const invalid=core.createClient({fetcher:async()=>new Response('<html>unavailable</html>',{status:503}),onConnection:x=>connections.push(x)});await assert.rejects(invalid('/health'),e=>e.code==='INVALID_RESPONSE'&&e.message.includes('503'));assert.equal(connections.at(-1),false);
});
test('write timeouts never retry a potentially completed mutation, and caller cancellation is distinct',async()=>{
 let calls=0;const client=core.createClient({fetcher:async(_url,options)=>{calls++;return new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true}));}});
 await assert.rejects(client('/write',{method:'POST',timeout:10}),e=>e.code==='TIMEOUT'&&e.message.includes('避免重複'));assert.equal(calls,1);
 const cancel=new AbortController();const pending=client('/read',{signal:cancel.signal});cancel.abort();await assert.rejects(pending,e=>e.code==='CANCELLED');
});
test('draft backup round trip retains unfinished parameter text without IDs or automatic execution',()=>{
 const draft={format:'QE_NOTEBOOK_DRAFT',version:1,notebook:{id:'notebook-private',revision:8,name:'測試',source:'def strategy(df, params):\n    return df.close * 0',paramsText:'{"window":',gridText:'{}',form:{capital:10000,mcEnabled:false},notes:'未完成研究',datasetIds:['ds-demo']}};
 const result=core.parseDraft(JSON.stringify(draft));assert.equal(result.paramsText,'{"window":');assert.equal(result.notes,'未完成研究');assert.equal(result.id,undefined);assert.equal(result.revision,undefined);
 assert.throws(()=>core.parseDraft(JSON.stringify({...draft,notebook:{...draft.notebook,form:JSON.parse('{"__proto__":{"polluted":true}}')}})),/不支援/);
 assert.throws(()=>core.parseDraft('{'),/JSON/);assert.throws(()=>core.parseDraft('x'.repeat(1048577)),/1 MB/);
 const legacy={...draft,notebook:{...draft.notebook,paramsText:undefined,params:{fast:5},gridText:undefined,grid:{fast:[3,5]}}};assert.deepEqual(JSON.parse(core.parseDraft(JSON.stringify(legacy)).paramsText),{fast:5});
});
test('quota errors report draft failure without throwing or claiming persistence',()=>{
 assert.equal(core.storeDraft({setItem(){throw Error('quota');}},'draft',{source:'x'}),false);
 let stored;assert.equal(core.storeDraft({setItem(_key,value){stored=value;}},'draft',{source:'x'}),true);assert.deepEqual(JSON.parse(stored),{source:'x'});
});
test('note save snapshots exclude edits made during a pending write and suppress duplicate submits',async()=>{
 const polish=await readFile(new URL('../app/polish.js',import.meta.url),'utf8'),start=polish.indexOf('saveNotebook=async function(){'),end=polish.indexOf('\nfunction recentHomeRows',start);
 let resolveWrite,posts=0;const messages=[],button={disabled:false,textContent:'保存筆記',isConnected:true},status={textContent:''};
 const quant={name:'note',source:'before',paramsText:'{}',gridText:'{}',form:{}},notebookDesk={revision:1,id:'note-id',dirty:true},ctx=vm.createContext({polish:{saving:false},quant,notebookDesk,notebookLayout:{rangesDirty:false},notebookSavedContent:null,researchShelf:{},accountStorage:{},QuantClientCore:{storeDraft:()=>true},captureNotebook(){},notebookContent:()=>JSON.stringify(quant),parseObject:JSON.parse,toast:text=>messages.push(text),shortDate:()=>'',esc:x=>x,$:selector=>selector==='#saveNotebook'?button:selector==='#notebookSaveState'?status:null,api:async(_url,options)=>{if(options?.method==='POST'){posts++;return new Promise(resolve=>resolveWrite=resolve);}throw Error('list refresh failed');}});
 vm.runInContext(polish.slice(start,end),ctx);const first=ctx.saveNotebook(),duplicate=ctx.saveNotebook();assert.equal(posts,1);assert.equal(button.disabled,true);quant.source='edited while saving';resolveWrite({id:'note-id',revision:2,updatedAt:'2026-10-27'});await Promise.all([first,duplicate]);assert.equal(notebookDesk.dirty,true);assert.equal(notebookDesk.revision,2);assert.equal(button.disabled,false);assert.match(status.textContent,/新修改/);assert.match(messages[0],/已保存/);
});
