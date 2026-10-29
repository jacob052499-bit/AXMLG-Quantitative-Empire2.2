import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {discoverPython} from '../lib/quantlab.mjs';
test('TradingView reference subset: open PnL, FIFO, end orders, closed-equity intrabar DD, published margin case and timing',async()=>{
 const env=await discoverPython();const r=await new Promise((resolve,reject)=>{let output='';const p=spawn(env.command,['-m','unittest','discover','-s','python','-p','test_tradingview.py'],{windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});p.stdout.on('data',x=>output+=x);p.stderr.on('data',x=>output+=x);p.on('error',reject);p.on('close',code=>resolve({code,output}));});assert.equal(r.code,0,r.output);assert.match(r.output,/Ran 8 tests/);
});
