import {readFile,readdir,stat} from 'node:fs/promises';import {createHash} from 'node:crypto';import path from 'node:path';import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('..',import.meta.url));
const files=[];async function walk(dir){for(const d of await readdir(path.join(root,dir),{withFileTypes:true})){const f=path.join(dir,d.name);if(d.isDirectory())await walk(f);else files.push(f.replaceAll('\\','/'));}}
for(const dir of ['app','lib','python','examples','docs','desktop','tools','deploy'])await walk(dir);
const errors=[],privateName=['AXMLG','Edge'].join(' '),privateFile=['axmlg','edge.py'].join('_');
for(const f of files){if(f.includes('__pycache__')||f.endsWith('.pyc'))continue;if(f.endsWith(privateFile))errors.push(f);if(/\.(?:py|mjs|js|cjs|html|md|json)$/.test(f)&&!f.startsWith('tools/')&&!f.endsWith('user-guide.html')&&!f.endsWith('使用教學.html')){const text=await readFile(path.join(root,f),'utf8');if(text.includes(privateName))errors.push(f+' contains private strategy name');if(/C:[\\/]+Users[\\/]+jacob/i.test(text))errors.push(f+' contains developer machine path');}}
const templates=(await import('../lib/quantlab.mjs')).PYTHON_TEMPLATES;
if(templates.some(t=>t.id.includes('axmlg')))errors.push('Private strategy template');
const manifest={product:'AXMLG Quantitative Empire',version:'2.3.0',passed:!errors.length,filesChecked:files.length,templates:templates.map(x=>x.id),dataPolicy:'Distribution must contain only data/.gitkeep; no accounts, setup token, profiles, workspaces, logs or archives.',errors};
console.log(JSON.stringify(manifest,null,2));if(errors.length)process.exitCode=1;
