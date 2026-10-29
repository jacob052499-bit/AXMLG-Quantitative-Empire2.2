import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {discoverPython} from '../lib/quantlab.mjs';
test('Conservative CFD execution: hand-calculated gaps, brackets, margin, quotes, fees, carry, insolvency and ledger',async()=>{
  const env=await discoverPython();assert.ok(env.available);
  const result=await new Promise((resolve,reject)=>{let output='';const p=spawn(env.command,['-m','unittest','discover','-s','python','-p','test_execution.py'],{windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});p.stdout.on('data',x=>output+=x);p.stderr.on('data',x=>output+=x);p.on('error',reject);p.on('close',code=>resolve({code,output}));});
  assert.equal(result.code,0,result.output);assert.match(result.output,/Ran 11 tests/);
});
