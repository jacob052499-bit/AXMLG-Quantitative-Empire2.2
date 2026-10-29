import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readFile, writeFile, mkdir, readdir, rename, appendFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import {RequestError} from './lib/request-error.mjs';
import {resolveLocalWorkspace} from './lib/local-workspace.mjs';
import {lockData} from './lib/instance-lock.mjs';
import {readDistribution,localDownload} from './lib/distribution.mjs';
import {streamArchive,validateJSON} from './lib/http-security.mjs';
import {capacityConfig,capacitySnapshot,ensureCapacity,usage} from './lib/capacity.mjs';
import { generateDemo, importDataset, barsToCSV, hash } from './lib/data.mjs';
import { runResearch, STRATEGIES, ENGINE_VERSION } from './lib/engine.mjs';
import { QuantLab, PYTHON_TEMPLATES } from './lib/quantlab.mjs';
import { researchReport } from './lib/report.mjs';
import { factorReport, uiRun } from './lib/research-tools.mjs';
import {chartSeries} from './lib/chart-series.mjs';
import {reconcile} from './lib/reconcile.mjs';
import {importReference} from './lib/reference-import.mjs';
import {interactiveReport} from './lib/interactive-report.mjs';
import { SOURCE_FILES } from './lib/source-files.mjs';
import {scanReport,scanRows} from './lib/scan-report.mjs';
import {ResearchCatalog} from './lib/research-catalog.mjs';
import {compareDrawdowns} from './lib/risk-comparison.mjs';

export async function startApp({distributionOverride=null}={}){
const root=path.dirname(fileURLToPath(import.meta.url));
const distribution=distributionOverride||await readDistribution(root);
const dataRoot=path.resolve(process.env.QUANT_DATA_DIR||path.join(root,'data'));
const port=Number(process.env.PORT||distribution.port||4311);
const APP_VERSION='2.3.0';
const sourceFiles=SOURCE_FILES;
const programHash=hash((await Promise.all(sourceFiles.map(p=>readFile(path.join(root,p),'utf8')))).join('\n'));
await lockData(dataRoot);
const archiveRoot=path.join(dataRoot,'source-archive');
if(process.env.QUANT_DEPLOYMENT!=='web'){
for(const file of sourceFiles){const dest=path.join(archiveRoot,programHash,file);await mkdir(path.dirname(dest),{recursive:true});await writeFile(dest,await readFile(path.join(root,file)));}
await writeFile(path.join(archiveRoot,programHash,'manifest.json'),JSON.stringify({programHash,sourceFiles}));
}
const web=process.env.QUANT_DEPLOYMENT==='web',portalOnly=web;
const publicOrigin=process.env.QUANT_PUBLIC_ORIGIN||'http://127.0.0.1:'+port;
if(web&&(!/^https:\/\//.test(publicOrigin)||new URL(publicOrigin).origin!==publicOrigin))throw Error('網頁模式必須指定完整 HTTPS QUANT_PUBLIC_ORIGIN。');
if(!web&&process.env.QUANT_BIND_HOST&&!['127.0.0.1','localhost','::1'].includes(process.env.QUANT_BIND_HOST))throw Error('本機版僅接受 loopback 綁定；遠端服務請使用 web 模式。');
const requestToken=randomUUID();
const localWorkspace=web?null:await resolveLocalWorkspace(dataRoot);
const limits=capacityConfig({...process.env,QUANT_ACCOUNT_STORAGE_GB:process.env.QUANT_ACCOUNT_STORAGE_GB||'20'});
const workspaces=new Map(),activeDownloads=new Map();let startingJobs=0;
async function startLimited(action){const all=await Promise.all([...workspaces.values()]);if(startingJobs+all.reduce((n,w)=>n+w.quantLab.active.size,0)>=limits.globalJobs)throw new RequestError("目前研究佇列已滿，請稍後再試。",429);startingJobs++;try{return await action();}finally{startingJobs--;}}
const tenantBase=dataRoot;
async function createWorkspace(userId){
 if(!/^user-[a-f0-9-]+$/.test(userId))throw Error('無效帳戶。');
 const dataRoot=localWorkspace.folder;await mkdir(dataRoot,{recursive:true,mode:0o700});
 await mkdir(path.join(dataRoot,'datasets'),{recursive:true});
await mkdir(path.join(dataRoot,'runs'),{recursive:true});
await mkdir(path.join(dataRoot,'attempts'),{recursive:true});
await mkdir(path.join(dataRoot,'run-index'),{recursive:true});
await mkdir(path.join(dataRoot,'dataset-index'),{recursive:true});
await mkdir(path.join(dataRoot,'batches'),{recursive:true});
await mkdir(path.join(dataRoot,'batch-index'),{recursive:true});
const catalog=await new ResearchCatalog(dataRoot).init(),datasetCache=new Map();
const read=async (kind,id)=>{
  if(!/^[a-z]+-[a-zA-Z0-9-]+$/.test(id))throw new Error('無效的紀錄 ID。');
  if(kind==='datasets'&&datasetCache.has(id))return datasetCache.get(id);
  const value=JSON.parse(await readFile(path.join(dataRoot,kind,`${id}.json`),'utf8'));
  if(kind==='datasets'){datasetCache.set(id,value);if(datasetCache.size>6)datasetCache.delete(datasetCache.keys().next().value);}return value;
};
const write=async(kind,id,value)=>{
  const dest=path.join(dataRoot,kind,`${id}.json`),temp=`${dest}.${randomUUID()}.tmp`;
  await writeFile(temp,JSON.stringify(value));await rename(temp,dest);
  if(kind==='runs')await write('run-index',id,summaryRun(value));
  if(kind==='datasets'){datasetCache.delete(id);await write('dataset-index',id,summaryDataset(value));}
};
const journal=async entry=>appendFile(path.join(dataRoot,'exposure.ndjson'),JSON.stringify({viewedAt:new Date().toISOString(),...entry})+'\n');
const capacityOptions={dataRoot:tenantBase,workspaceRoot:dataRoot,config:limits};
const quantLab=new QuantLab({root,dataRoot,archiveRoot,programHash,capacityOptions,saveRun:r=>write('runs',r.id,r),getDataset:id=>read('datasets',id),journal});
await quantLab.init();
for(const folder of ['reconciliations','reconciliation-index','reconciliation-inputs'])await mkdir(path.join(dataRoot,folder),{recursive:true});
const summaryDataset=d=>({id:d.id,name:d.name,identity:d.identity,quality:d.quality,fingerprint:d.fingerprint,createdAt:d.createdAt,preview:d.bars.slice(0,5)});
const summaryRun=r=>({summaryVersion:2,id:r.id,name:r.name,createdAt:r.createdAt,datasetId:r.datasetId,datasetName:r.datasetName,synthetic:r.synthetic,strategy:r.selected.strategy,mode:r.contract.config.mode,metrics:r.evaluation.metrics,execution_state:r.execution_state,research_decision:r.research_decision,activation_state:r.activation_state,reason_codes:r.reason_codes,capabilities:{ppTrials:r.analysis?.plateau?.cells?.length||0,ppAxes:Object.keys(r.analysis?.plateau?.axes||{}).length,mcSimulations:r.analysis?.monteCarlo?.simulations||0,quick:!!r.contract.python?.tools?.quick}});
async function list(kind) {
  const names=(await readdir(path.join(dataRoot,kind))).filter(x=>x.endsWith('.json'));
  const items=await Promise.all(names.map(async name=>{try{return JSON.parse(await readFile(path.join(dataRoot,kind,name),'utf8'));}catch{return null;}}));
  return items.filter(Boolean).sort((a,b)=>b.createdAt.localeCompare(a.createdAt));
}
const demo=generateDemo();
try{await read('datasets',demo.id);}catch{await write('datasets',demo.id,demo);}
for(const name of (await readdir(path.join(dataRoot,'datasets'))).filter(n=>n.endsWith('.json'))){try{await readFile(path.join(dataRoot,'dataset-index',name));}catch{const d=await read('datasets',name.slice(0,-5));await write('dataset-index',d.id,summaryDataset(d));}}
// Backfill compact summaries once; bootstrap never reparses entire H1 ledgers.
for(const name of (await readdir(path.join(dataRoot,'runs'))).filter(n=>/^run-.*\.json$/.test(n))){
  try{const s=JSON.parse(await readFile(path.join(dataRoot,'run-index',name),'utf8'));if(s.summaryVersion!==2)throw Error('Upgrade summary');}catch{const r=await read('runs',name.slice(0,-5));await write('run-index',r.id,summaryRun(r));}
}
if(!(await readdir(path.join(dataRoot,'run-index'))).length) {
  const id=`run-${randomUUID()}`;
  const config={name:'趨勢基準 · 初始示範',strategy:'sma',fast:20,slow:60,mode:'single'};
  const result=runResearch(demo,config,{id,programHash});
  await write('runs',id,result);
  await write('attempts',`attempt-${randomUUID()}`,{createdAt:new Date().toISOString(),status:'COMPLETED',runId:id,config,datasetId:demo.id});
}

 return {dataRoot,catalog,quantLab,read,write,list,journal,summaryDataset,summaryRun,capacityOptions};
}
function getWorkspace(userId){if(!workspaces.has(userId)){const pending=createWorkspace(userId).catch(e=>{workspaces.delete(userId);throw e;});workspaces.set(userId,pending);}return workspaces.get(userId);}
async function body(req) {
  if(!req.headers['content-type']?.includes('application/json'))throw new Error('請使用 application/json。');
  const chunks=[];let size=0;
  for await(const chunk of req){size+=chunk.length;if(size>64*1024*1024)throw new Error('匯入上限為 64 MB。');chunks.push(chunk);}
  try{return validateJSON(JSON.parse(Buffer.concat(chunks).toString('utf8')));}catch{throw new Error('JSON 格式無效。');}
}
const csvCell=v=>{const s=String(v??'');return /[",\n\r]/.test(s)?`"${s.replaceAll('"','""')}"`:s;};
const csvRows=(rows,keys)=>keys.join(',')+'\r\n'+rows.map(r=>keys.map(k=>csvCell(r[k])).join(',')).join('\r\n');
const types={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.svg':'image/svg+xml'};
const server=http.createServer({requestTimeout:30000,headersTimeout:10000,maxHeaderSize:16384},async(req,res)=>{
  res.setHeader('X-Content-Type-Options','nosniff');
  if(web)res.setHeader('Strict-Transport-Security','max-age=31536000');
  res.setHeader('Referrer-Policy','no-referrer');
  res.setHeader('Permissions-Policy','camera=(), microphone=(), geolocation=(), payment=()');
  res.setHeader('X-Frame-Options','DENY');
  res.setHeader('Cache-Control','no-store');
  res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'");
  const json=(v,status=200)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8'});res.end(JSON.stringify(v));};
  const download=(content,type,name)=>{res.writeHead(200,{'Content-Type':type,'Content-Disposition':`attachment; filename="${name}"`});res.end(content);};
  try{
    const host=req.headers.host||'';
    const allowedHosts=new Set(['127.0.0.1:'+port,'localhost:'+port,new URL(publicOrigin).host]);
    if(!allowedHosts.has(host))return json({error:'拒絕此主機名稱。'},403);
    const origins=new Set(web?[publicOrigin]:['http://127.0.0.1:'+port,'http://localhost:'+port]);
    if(req.headers.origin&&!origins.has(req.headers.origin))return json({error:'拒絕跨來源請求。'},403);
    const url=new URL(req.url,'http://127.0.0.1:'+port),p=url.pathname;
    if(req.method==='GET'&&p==='/api/health')return json({ok:true,version:APP_VERSION,programHash,pid:process.pid,workspaceId:hash(path.resolve(root).replace(/[\\/]+$/,'').toLowerCase())});
    if(['/login','/setup','/local-setup','/activate','/account','/admin'].includes(p)){res.writeHead(303,{Location:'/'});return res.end();}
    if(p.startsWith('/api/auth/')||p.startsWith('/api/admin/'))return json({error:'帳號與授權功能已移除。',code:'FEATURE_REMOVED'},410);
    const publicFiles={'/user-guide.html':'user-guide.html','/help':'user-guide.html','/user-guide.js':'user-guide.js','/downloads.js':'downloads.js','/downloads.css':'downloads.css'};
    if(req.method==='GET'&&(publicFiles[p]||p==='/brand.svg')){const file=p==='/brand.svg'?path.join(root,'desktop','icon.svg'):path.join(root,'app',publicFiles[p]);res.writeHead(200,{'Content-Type':types[path.extname(file)]});return res.end(await readFile(file));}
    if(req.method==='GET'&&p==='/api/workspace')return json({id:localWorkspace?.id||null,requiresLogin:false,portalOnly,requestToken:portalOnly?undefined:requestToken});
    if(req.method==='GET'&&p==='/workspace-config.js'){res.writeHead(200,{'Content-Type':types['.js']});return res.end('window.QuantWorkspace='+JSON.stringify({id:localWorkspace?.id,requestToken,localOnly:!web}).replaceAll('<','\\u003c')+';');}
    if(req.method==='GET'&&p==='/local-client.js'){res.writeHead(200,{'Content-Type':types['.js']});return res.end(await readFile(path.join(root,'app/local-client.js')));}
    if(!portalOnly&&!['GET','HEAD'].includes(req.method)&&req.headers['x-qe-csrf']!==requestToken)return json({error:'頁面連線已更新，請重新整理後再試。',code:'REQUEST_TOKEN_REQUIRED'},403);
    if(req.method==='GET'&&p==='/api/downloads'){const {file,...record}=await localDownload(root);return json(record);}
    if(['GET','HEAD'].includes(req.method)&&p==='/api/downloads/local'){const userId='local',total=[...activeDownloads.values()].reduce((a,b)=>a+b,0);if((activeDownloads.get(userId)||0)>=2||total>=16)throw new RequestError('目前下載連線已滿，請稍後重試。',429,'DOWNLOAD_LIMIT');activeDownloads.set(userId,(activeDownloads.get(userId)||0)+1);try{return await streamArchive(req,res,await localDownload(root));}finally{const remaining=activeDownloads.get(userId)-1;if(remaining)activeDownloads.set(userId,remaining);else activeDownloads.delete(userId);}}
    if(req.method==='GET'&&p==='/api/help/download')return download(await readFile(path.join(root,'docs','AXMLG Quantitative Empire 使用教學.html')),'text/html; charset=utf-8','AXMLG-user-guide-v'+APP_VERSION+'.html');
    if(req.method==='GET'&&p==='/downloads'){res.writeHead(200,{'Content-Type':types['.html']});return res.end(await readFile(path.join(root,'app/downloads.html')));}
    if(req.method==='GET'&&p==='/api/portal')return json({portalOnly,requiresLogin:false,localResearch:!portalOnly});
    if(portalOnly){if(req.method==='GET'&&(p==='/'||p==='/index.html')){res.writeHead(303,{Location:'/downloads'});return res.end();}return json({error:'此網站只負責下載；請在本機版執行研究與回測。',code:'DOWNLOAD_PORTAL_ONLY'},403);}
    const {dataRoot,catalog,quantLab,read,write,list,journal,summaryDataset,summaryRun,capacityOptions}=await getWorkspace(localWorkspace.id);
    if(req.method==='GET'&&p==='/api/capacity')return json(await capacitySnapshot({...capacityOptions,activeJobs:[...workspaces.values()].length? (await Promise.all([...workspaces.values()])).reduce((n,w)=>n+w.quantLab.active.size,0):0,includeHost:true}));
    if(req.method==='POST'&&!p.endsWith('/cancel')&&!['/api/research-entries','/api/research-folders'].includes(p))await ensureCapacity(capacityOptions,{incoming:Math.min(Number(req.headers['content-length'])||0,64*1024*1024),strategy:p==='/api/python/strategies',job:p==='/api/python/jobs'});
    if(req.method==='GET'&&p==='/api/bootstrap'){
      const [datasets,runs]=await Promise.all([list('dataset-index'),list('run-index')]);
      return json({datasets,runs:catalog.visible('run',runs),strategies:{...STRATEGIES,python:{name:'Python 自訂策略',description:'本機 Python / NumPy / pandas 研究',defaults:{fast:20,slow:60}}},version:APP_VERSION,storage:'./data',programHash});
    }
    if(req.method==='GET'&&p==='/api/research-library'){
      const pairs=await Promise.all([['run','run-index'],['factor','factor-index'],['scan','scan-index'],['batch','batch-index'],['strategy','strategies']].map(async([kind,dir])=>(await list(dir)).map(({source,...r})=>({...r,kind}))));
      const notebooks=catalog.value.notebooks.map(({source,params,grid,form,hypothesis,logic,notes,...n})=>({...n,kind:'notebook'}));
      return json({folders:catalog.value.folders,items:[...pairs.flat(),...notebooks].map(r=>({...r,...catalog.value.entries[`${r.kind}:${r.id}`],name:catalog.value.entries[`${r.kind}:${r.id}`]?.name||r.name})).sort((a,b)=>b.createdAt.localeCompare(a.createdAt))});
    }
    if(req.method==='POST'&&p==='/api/research-folders')return json(await catalog.folder(await body(req)));
    if(req.method==='POST'&&p==='/api/research-entries')return json(await catalog.organize(await body(req),async(kind,id)=>{if(kind==='notebook')return catalog.value.notebooks.some(x=>x.id===id);const dirs={run:'run-index',factor:'factor-index',scan:'scan-index',strategy:'strategies',batch:'batch-index'};try{await read(dirs[kind],id);return true;}catch{return false;}}));
    if(req.method==='GET'&&p==='/api/notebooks')return json(catalog.visible('notebook',catalog.value.notebooks));
    if(req.method==='POST'&&p==='/api/notebooks')return json(await catalog.notebook(await body(req)),201);
    if(req.method==='GET'&&/^\/api\/batches\/batch-[a-f0-9-]+(?:\/(?:bundle|csv))?$/.test(p)){
      const parts=p.split('/'),r=await read('batches',parts[3]);await journal({batchId:r.id,purpose:parts[4]||'BATCH_VIEW',evidence:'KNOWN_EVALUATION'});
      if(parts[4]==='bundle')return download(JSON.stringify({format:'QE_BATCH_BUNDLE',version:1,record:r,datasets:await Promise.all(r.contract.datasetIds.map(id=>read('datasets',id))),runs:await Promise.all(r.items.filter(x=>x.runId).map(x=>read('runs',x.runId)))}),'application/json; charset=utf-8',r.id+'-bundle.json');
      if(parts[4]==='csv'){const rows=r.items.map(x=>({datasetId:x.datasetId,symbol:x.symbol,timeframe:x.timeframe,currency:x.currency,status:x.status,runId:x.runId||'',netPnl:x.metrics?.netPnl,return:x.metrics?.totalReturn,maxDrawdownAmount:x.metrics?.reportMaxDrawdownAmount??x.metrics?.maxDrawdownAmount,sharpe:x.metrics?.sharpe,error:x.error||''}));return download(csvRows(rows,Object.keys(rows[0]||{})),'text/csv; charset=utf-8',r.id+'.csv');}
      return json(r);
    }
    if(req.method==='GET'&&p==='/api/factor-methods')return json(JSON.parse(await readFile(path.join(root,'docs','factor-literature.json'),'utf8')));
    if(req.method==='GET'&&p==='/api/python/environment'){const {command,executable,...environment}=await quantLab.environment;return json(environment);}
    if(req.method==='GET'&&p==='/api/python/templates')return json(PYTHON_TEMPLATES);
    if(req.method==='GET'&&p==='/api/python/strategies')return json(catalog.visible('strategy',await quantLab.strategies()));
    if(req.method==='POST'&&p==='/api/python/strategies')return json(await quantLab.saveStrategy(await body(req)),201);
    if(req.method==='GET'&&p==='/api/python/jobs')return json((await quantLab.listJobs()).slice(0,30).map(({source,console,...job})=>({...job,console:console.slice(-2000)})));
    if(req.method==='POST'&&p==='/api/python/jobs')return json(await startLimited(async()=>quantLab.create(await body(req))),201);
    if(req.method==='GET'&&/^\/api\/python\/jobs\/job-[a-f0-9-]+$/.test(p))return json(await quantLab.getJob(p.split('/').at(-1)));
    if(req.method==='POST'&&/^\/api\/python\/jobs\/job-[a-f0-9-]+\/cancel$/.test(p))return json({cancelled:await quantLab.stop(p.split('/').at(-2))});
    if(req.method==='POST'&&/^\/api\/python\/jobs\/job-[a-f0-9-]+\/resume$/.test(p))return json(await startLimited(()=>quantLab.resume(p.split('/').at(-2))),201);
    if(req.method==='GET'&&p==='/api/factors')return json(catalog.visible('factor',await quantLab.factorSummaries()));
    if(req.method==='GET'&&p==='/api/scans')return json(catalog.visible('scan',await list('scan-index')));
    if(req.method==='GET'&&/^\/api\/scans\/scan-[a-f0-9-]+(?:\/(?:csv|report|bundle))?$/.test(p)){
      const parts=p.split('/'),r=await read('scans',parts[3]);await journal({scanId:r.id,purpose:parts[4]||'SCAN_VIEW',evidence:'KNOWN_EVALUATION'});
      if(parts[4]==='csv'){const rows=scanRows(r);return download(csvRows(rows,Object.keys(rows[0]||{})),'text/csv; charset=utf-8',r.id+'.csv');}
      if(parts[4]==='report')return download(scanReport(r),'text/html; charset=utf-8',r.id+'-report.html');
      if(parts[4]==='bundle')return download(JSON.stringify({format:'QE_SCAN_BUNDLE',version:1,record:r,datasets:await Promise.all(r.contract.datasetIds.map(id=>read('datasets',id))),factors:await Promise.all(r.items.filter(x=>x.factorId).map(x=>read('factors',x.factorId)))}),'application/json; charset=utf-8',r.id+'-bundle.json');
      return json(r);
    }
    if(req.method==='GET'&&/^\/api\/factors\/factor-[a-f0-9-]+(?:\/(?:report|bundle|nav|fills|scores|strategy\.py))?$/.test(p)){
      const parts=p.split('/'),r=await read('factors',parts[3]);
      await journal({factorId:r.id,datasetIds:r.contract.datasetIds,purpose:parts[4]||'FACTOR_VIEW',evidence:'KNOWN_EVALUATION'});
      if(parts[4]==='strategy.py'){if(!r.strategyDraft?.source)throw Error('此研究沒有單商品 Python 草稿；請重跑 EL1 時序研究。');return download(r.strategyDraft.source,'text/x-python; charset=utf-8',r.id+'-EL1.py');}
      if(parts[4]==='report')return download(await interactiveReport({...r,full:null},factorReport(r)),'text/html; charset=utf-8',r.id+'-report.html');
      if(parts[4]==='bundle')return download(JSON.stringify({format:'QE_FACTOR_BUNDLE',version:1,record:r,datasets:await Promise.all(r.contract.datasetIds.map(id=>read('datasets',id)))}),'application/json; charset=utf-8',r.id+'-bundle.json');
      if(parts[4]==='nav')return download(csvRows(r.evaluation.nav,['date','nav','cash','quantity','return','pnl','drawdown','commission']),'text/csv; charset=utf-8',r.id+'-nav.csv');
      if(['trades','ledger','rejections'].includes(parts[4])){
        const kind=parts[4],section=r.evaluation,columns={trades:['id','cycleId','orderId','entryTime','exitTime','entryPrice','exitPrice','quantity','grossPnl','commission','financing','netPnl','barsHeld','exitReason','timePrecision'],ledger:['date','closeTime','nav','cash','quantity','close','marginUsed','freeMargin','marginLevel','unrealizedPnl','realizedGrossPnl','commission','financing','cumulativeCommission','cumulativeFinancing','accountingError','riskHalted'],rejections:['index','time','id','requestedQuantity','reason']};
        return download(csvRows(kind==='ledger'?section.nav:section[kind]||[],columns[kind]),'text/csv; charset=utf-8',`${r.id}-${kind}.csv`);
      }
      if(parts[4]==='fills')return download(csvRows(r.evaluation.fills,r.mode==='single'?['time','decisionTime','side','quantity','price','commission','reason']:['time','decisionTime','featureAvailableTime','symbol','side','quantity','reference','notional','cost','reason']),'text/csv; charset=utf-8',r.id+'-fills.csv');
      if(parts[4]==='scores')return download(csvRows(r.current,['symbol','score','target']),'text/csv; charset=utf-8',r.id+'-scores.csv');
      if(url.searchParams.get('view')==='ui'){const v=uiRun({...r,selected:r.config,contract:{...r.contract,dataIdentity:r.quality.dataIdentity?.[0]}});return json({...v,selected:r.selected,contract:r.contract});}return json(r);
    }
    if(req.method==='POST'&&p==='/api/datasets'){
      const b=await body(req),dataset=importDataset(b.csv,b.metadata);
      await write('datasets',dataset.id,dataset);
      return json(summaryDataset(dataset),201);
    }
    if(req.method==='GET'&&/^\/api\/datasets\/[^/]+\/download$/.test(p)){
      const d=await read('datasets',p.split('/')[3]);
      await journal({datasetId:d.id,purpose:'DATA_DOWNLOAD',evidence:'KNOWN_EVALUATION',datasetHash:d.fingerprint});
      return download(barsToCSV(d),'text/csv; charset=utf-8',`${d.id}.csv`);
    }
    if(req.method==='POST'&&p==='/api/runs'){
      const b=await body(req),attemptId=`attempt-${randomUUID()}`,createdAt=new Date().toISOString();
      const attempt={createdAt,status:'RUNNING',datasetId:b.datasetId,config:b.config,programHash};
      await write('attempts',attemptId,attempt);
      try{
        const d=await read('datasets',String(b.datasetId));
        const result=runResearch(d,b.config,{id:`run-${randomUUID()}`,programHash});
        result.attemptId=attemptId;
        await write('runs',result.id,result);
        await write('attempts',attemptId,{...attempt,status:'COMPLETED',runId:result.id,completedAt:new Date().toISOString()});
        await journal({datasetId:d.id,runId:result.id,purpose:'RUN_RESULT',period:result.contract.split,evidence:'KNOWN_EVALUATION',datasetHash:d.fingerprint});
        return json(result,201);
      }catch(e){await write('attempts',attemptId,{...attempt,status:'FAILED',reason:e.message,completedAt:new Date().toISOString()});throw e;}
    }
    if(req.method==='GET'&&/^\/api\/runs\/[^/]+\/risk-comparison$/.test(p)){
      const r=await read('runs',p.split('/')[3]),requested=url.searchParams.get('scope')==='full'?'full':'evaluation',peer=url.searchParams.get('peer')||'baseline';
      let scope=requested,a,b,nameB='同成本買入持有基準',other=null,scopeNote=null;
      if(peer==='baseline'){if(scope==='full'&&(!r.full||!r.fullBaseline)){scope='evaluation';scopeNote='舊紀錄未保存全期間策略或基準，本圖明確改用兩方皆已保存的 OOS；新回測會保存全期間基準。';}a=scope==='full'?r.full:r.evaluation;b=scope==='full'?r.fullBaseline:r.baseline;}
      else{other=await read('runs',peer);if(scope==='full'&&(!r.full||!other.full)){scope='evaluation';scopeNote='其中一份報告缺少全期間帳本；本圖明確改用兩份報告的 OOS，並只比較共同觀測時間。';}a=scope==='full'?r.full:r.evaluation;b=scope==='full'?other.full:other.evaluation;nameB=other.name;}
      const currency=r.contract.dataIdentity?.currency||(await read('datasets',r.datasetId)).identity.currency;
      if(other){const otherCurrency=other.contract.dataIdentity?.currency||(await read('datasets',other.datasetId)).identity.currency;if(currency!==otherCurrency)throw Error('兩份報告計價幣別不同，請先提供明確的匯率轉換資料。');}
      const comparison=compareDrawdowns(a,b,{capital:Number(url.searchParams.get('capital')||r.selected.capital),target:url.searchParams.get('target')||'minimum',customTarget:url.searchParams.get('amount'),currency,names:[r.name,nameB]});
      return json({...comparison,scope,requestedScope:requested,scopeNote,runId:r.id,peer});
    }
    if(/^\/api\/runs\/[^/]+\/(charts|ledger-view|reconciliations|reconcile)$/.test(p)){
      const parts=p.split('/'),r=await read('runs',parts[3]),kind=parts[4];
      if(req.method==='GET'&&kind==='charts')return json(chartSeries(await read('datasets',r.datasetId),r,url.searchParams.get('scope')||'full'));
      if(req.method==='GET'&&kind==='ledger-view'){
        const scope=url.searchParams.get('scope')==='evaluation'?'evaluation':'full',section=scope==='full'&&r.full?r.full:r.evaluation,k=url.searchParams.get('kind')||'trades';
        if(!['trades','fills','rejections','openTrades'].includes(k))throw Error('無效的帳本類型。');
        const search=(url.searchParams.get('search')||'').toLowerCase(),outcome=url.searchParams.get('outcome')||'all',sort=url.searchParams.get('sort')||'entryTime',ascending=url.searchParams.get('direction')==='asc';
        if(!['entryTime','exitTime','netPnl','quantity','time'].includes(sort))throw Error('無效的排序欄位。');
        let rows=(section[k]||[]).filter(x=>(!search||[x.id,x.orderId,x.reason,x.exitReason,x.entryTime,x.exitTime,x.time].join(' ').toLowerCase().includes(search))&&(outcome==='all'||(outcome==='win'?x.netPnl>0:x.netPnl<0)));
        rows=rows.slice().sort((a,b)=>{const x=a[sort]??'',y=b[sort]??'';return (typeof x==='number'&&typeof y==='number'?x-y:String(x).localeCompare(String(y)))*(ascending?1:-1);});
        const rawPage=Number(url.searchParams.get('page')||1),rawSize=Number(url.searchParams.get('pageSize')||100);if(!Number.isInteger(rawPage)||rawPage<1||!Number.isInteger(rawSize)||rawSize<1||rawSize>200)throw Error('無效分頁。');
        return json({runId:r.id,scope,total:rows.length,page:rawPage,pageSize:rawSize,rows:rows.slice((rawPage-1)*rawSize,rawPage*rawSize)});
      }
      if(req.method==='GET'&&kind==='reconciliations')return json((await list('reconciliation-index')).filter(x=>x.runId===r.id));
      if(req.method==='POST'&&kind==='reconcile'){
        const b=await body(req),reference=await importReference(root,await quantLab.environment,b),result=reconcile(r,reference,b),id='reconciliation-'+randomUUID(),createdAt=new Date().toISOString(),source=b.format==='xlsx'?Buffer.from(b.data,'base64'):Buffer.from(b.csv||'','utf8');
        const record={id,runId:r.id,createdAt,name:String(b.name||'TradingView 匯出').slice(0,160),sourceHash:hash(source.toString('base64')),format:b.format,reference,...result};
        await writeFile(path.join(dataRoot,'reconciliation-inputs',id+'.'+b.format),source);await write('reconciliations',id,record);
        const {results,reference:ref,openResults,...summary}=record;await write('reconciliation-index',id,summary);
        await journal({runId:r.id,purpose:'TRADINGVIEW_RECONCILIATION',referenceHash:record.sourceHash,evidence:'KNOWN_EVALUATION'});return json(record,201);
      }
    }
    if(req.method==='GET'&&/^\/api\/reconciliations\/reconciliation-[a-f0-9-]+$/.test(p))return json(await read('reconciliations',p.split('/')[3]));
    if(req.method==='GET'&&/^\/api\/runs\/[^/]+(?:\/(?:bundle|fills|nav|report|daily-nav|trades|ledger|rejections|full-nav|full-fills|full-cycles|full-trades|full-ledger|full-rejections))?$/.test(p)){
      const parts=p.split('/'),r=await read('runs',parts[3]);
      await journal({runId:r.id,datasetId:r.datasetId,purpose:parts[4]||'RESULT_VIEW',evidence:'KNOWN_EVALUATION',period:r.contract.split});
      if(parts[4]==='report')return download(await interactiveReport(r,researchReport(r)),'text/html; charset=utf-8',`${r.id}-report.html`);
      if(parts[4]==='bundle') {
        const dataset=await read('datasets',r.datasetId);
        const attempts=(await list('attempts')).filter(a=>a.datasetId===r.datasetId);
        const exposure=(await readFile(path.join(dataRoot,'exposure.ndjson'),'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse).filter(x=>x.datasetId===r.datasetId);
        return download(JSON.stringify({format:'QUANTITATIVE_EMPIRE_RESEARCH_BUNDLE',version:1,exportedAt:new Date().toISOString(),environment:{node:process.version,platform:process.platform},run:r,dataset,attempts,exposure},null,2),'application/json; charset=utf-8',`${r.id}-bundle.json`);
      }
      if(['trades','ledger','rejections'].includes(parts[4])){
        const kind=parts[4],section=r.evaluation,columns={trades:['id','cycleId','orderId','entryTime','exitTime','entryPrice','exitPrice','quantity','grossPnl','commission','financing','netPnl','barsHeld','exitReason','timePrecision'],ledger:['date','closeTime','nav','cash','quantity','close','marginUsed','freeMargin','marginLevel','unrealizedPnl','realizedGrossPnl','commission','financing','cumulativeCommission','cumulativeFinancing','accountingError','riskHalted'],rejections:['index','time','id','requestedQuantity','reason']};
        return download(csvRows(kind==='ledger'?section.nav:section[kind]||[],columns[kind]),'text/csv; charset=utf-8',`${r.id}-${kind}.csv`);
      }
      if(parts[4]==='fills')return download(csvRows(r.evaluation.fills,['index','side','time','decisionTime','featureAvailableTime','sequence','reference','price','quantity','commission','executionDrag','reason']),'text/csv; charset=utf-8',`${r.id}-fills.csv`);
      if(parts[4]==='nav')return download(csvRows(r.evaluation.nav,['date','nav','cash','quantity','close','return','pnl','drawdown','commission','executionDrag','hadPosition']),'text/csv; charset=utf-8',`${r.id}-nav.csv`);
      if(parts[4]==='daily-nav')return download(csvRows(r.evaluation.dailyNav||r.evaluation.nav,['date','nav','cash','quantity','return','pnl','drawdown','commission','executionDrag','hadPosition']),'text/csv; charset=utf-8',`${r.id}-daily-nav.csv`);
      if(parts[4]?.startsWith('full-')){
        if(!r.full)throw new Error('此研究沒有全期間事件式回測。');
        const kind=parts[4].slice(5),columns={ledger:['date','closeTime','nav','cash','quantity','close','marginUsed','freeMargin','marginLevel','unrealizedPnl','realizedGrossPnl','commission','financing','cumulativeCommission','cumulativeFinancing','accountingError','riskHalted'],rejections:['index','time','id','requestedQuantity','reason'],nav:['date','nav','cash','quantity','close','return','pnl','drawdown','commission','executionDrag','marginUsed','openEntries'],fills:['index','side','time','barOpen','barClose','timePrecision','eventPhase','decisionTime','orderId','reference','price','quantity','commission','executionDrag','reason'],cycles:['id','entryTime','exitTime','legCount','quantity','entryPrice','exitPrice','netPnl','commission','financing','barsHeld','exitReason'],trades:['id','cycleId','orderId','entryTime','exitTime','quantity','entryPrice','exitPrice','grossPnl','commission','financing','netPnl','barsHeld','exitReason','timePrecision']};
        return download(csvRows(kind==='ledger'?r.full.nav:r.full[kind]||[],columns[kind]),'text/csv; charset=utf-8',`${r.id}-full-${kind}.csv`);
      }
      return json(url.searchParams.get('view')==='ui'?uiRun(r):r);
    }
    if(req.method!=='GET')return json({error:'找不到此操作。'},404);
    const files={'/capacity.js':'capacity.js','/account-ui.css':'account-ui.css','/client-core.js':'client-core.js','/polish.js':'polish.js','/polish.css':'polish.css','/':'index.html','/index.html':'index.html','/user-guide.js':'user-guide.js','/help':'user-guide.html','/user-guide.html':'user-guide.html','/app.js':'app.js','/workbench.js':'workbench.js','/research.js':'research.js','/charts.js':'charts.js','/execution.js':'execution.js','/interactive.js':'interactive.js','/studio.js':'studio.js','/studio.css':'studio.css','/refined.css':'refined.css','/report-metrics.js':'report-metrics.js','/factor-studio.js':'factor-studio.js','/workspace.js':'workspace.js','/workspace.css':'workspace.css','/black.css':'black.css','/factor-scanner.js':'factor-scanner.js','/vendor/echarts.min.js':'vendor/echarts.min.js','/styles.css':'styles.css','/workbench.css':'workbench.css'};
    for(const asset of ['professional.js','research-flow.js','research-desk.js','research-diagnostics.js','factor-education.js','research-desk.css'])files['/'+asset]=asset;
    const file=files[p];if(!file)return json({error:'找不到此檔案。'},404);
    const content=await readFile(path.join(root,'app',file));res.writeHead(200,{'Content-Type':types[path.extname(file)]});res.end(content);
  }catch(e){if(res.headersSent){if(!res.destroyed)res.destroy();return;}const internal=['EACCES','EPERM','ENOSPC','EIO','EMFILE'].includes(e.code);json({error:e.code==='ENOENT'?'找不到紀錄。':internal?'服務暫時無法處理，請聯絡營運方。':e.message,code:internal?'STORAGE_ERROR':e.code||'REQUEST_FAILED'},e.status||(e.code==='ENOENT'?404:internal?503:400));}
});
server.listen(port,process.env.QUANT_BIND_HOST||'127.0.0.1',()=>console.log(`AXMLG Quantitative Empire v${APP_VERSION}\nLocal: http://127.0.0.1:${port}\nData: ${dataRoot}`));
server.on('error',e=>{console.error(e.code==='EADDRINUSE'?`Port ${port} is already in use. Open http://127.0.0.1:${port} or set PORT.`:e.message);process.exitCode=1;});

return server;
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))await startApp();
