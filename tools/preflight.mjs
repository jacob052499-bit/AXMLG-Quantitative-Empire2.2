import {readDistribution,localDownload} from '../lib/distribution.mjs';import {discoverPython} from '../lib/quantlab.mjs';import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('..',import.meta.url)),d=await readDistribution(root),web=process.env.QUANT_DEPLOYMENT==='web'||process.argv.includes('--web'),checks=[];
checks.push({name:'Node.js',ok:Number(process.versions.node.split('.')[0])>=22});
if(web){let validOrigin=false;try{const u=new URL(process.env.QUANT_PUBLIC_ORIGIN);validOrigin=u.protocol==='https:'&&u.origin===process.env.QUANT_PUBLIC_ORIGIN;}catch{}checks.push({name:'HTTPS origin',ok:validOrigin});const r=await localDownload(root);checks.push({name:'Clean direct-use archive',ok:r.available&&r.requiresLogin===false&&r.requiresLicense===false});}
else{const e=await discoverPython();checks.push({name:'Bundled Python',ok:e.available,version:e.python});}
console.log(JSON.stringify({product:d.product,version:d.version,requiresLogin:false,passed:checks.every(c=>c.ok),checks},null,2));if(checks.some(c=>!c.ok))process.exitCode=1;
