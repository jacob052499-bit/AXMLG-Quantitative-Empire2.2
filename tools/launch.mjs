import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { mkdir, open as openFile, writeFile,readFile } from 'node:fs/promises';

const root=fileURLToPath(new URL('..',import.meta.url));
const distribution=JSON.parse(await readFile(path.join(root,'distribution.json'),'utf8'));
const port=Number(process.env.PORT||distribution.port||4311),url=`http://127.0.0.1:${port}`;
const background=process.argv.includes('--background'),noOpen=process.argv.includes('--no-open');
const runtime=path.resolve(process.env.QUANT_RUNTIME_DIR||path.join(root,'.runtime'));
async function healthy(){try{const r=await fetch(url+'/api/health',{signal:AbortSignal.timeout(700)});const b=await r.json();return b.ok===true&&typeof b.programHash==='string'&&b.programHash.length===64;}catch{return false;}}
function open(){if(noOpen)return;const command=process.platform==='win32'?'rundll32.exe':process.platform==='darwin'?'open':'xdg-open';const args=process.platform==='win32'?['url.dll,FileProtocolHandler',url]:[url];const child=spawn(command,args,{stdio:'ignore',windowsHide:true});child.on('error',()=>console.log(`Open ${url} in your browser.`));child.unref();}
if(await healthy()){console.log(`App is already running at ${url}`);open();}
else{
  await mkdir(runtime,{recursive:true});
  const log=background?await openFile(path.join(runtime,'server.log'),'a'):null;
  const errors=background?await openFile(path.join(runtime,'server-error.log'),'a'):null;
  const child=spawn(process.execPath,[path.join(root,'server.mjs')],{cwd:root,stdio:background?['ignore',log.fd,errors.fd]:'inherit',env:process.env,windowsHide:true,detached:background});
  let launchError=null;
  child.on('error',e=>{launchError=e;console.error(e.message);process.exitCode=1;});
  child.on('exit',code=>{if(!background||code)process.exitCode=code||0;});
  let ready=false;
  for(let i=0;i<40;i++){if(launchError||child.exitCode!==null)break;if(await healthy()){ready=true;break;}await new Promise(r=>setTimeout(r,150));}
  if(ready){
    if(background){await writeFile(path.join(runtime,`server-${port}.json`),JSON.stringify({pid:child.pid,port,root,server:path.join(root,'server.mjs'),startedAt:new Date().toISOString()}));child.unref();}
    console.log(`App ready: ${url}`);open();
  }else{console.error(`App did not start. See ${path.join(runtime,'server-error.log')}`);process.exitCode=1;if(child.exitCode===null&&!launchError)child.kill();}
  await log?.close();await errors?.close();
}
