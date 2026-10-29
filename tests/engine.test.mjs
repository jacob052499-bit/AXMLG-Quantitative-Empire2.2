import test from 'node:test';
import assert from 'node:assert/strict';
import { DAY, generateDemo, importDataset, barsToCSV, hash, parseTimestamp } from '../lib/data.mjs';
import { normalizeConfig, simulate, features, runResearch } from '../lib/engine.mjs';

const approx=(a,b,tolerance=1e-8)=>assert.ok(Math.abs(a-b)<=tolerance,`${a} != ${b}`);
function flatBars(n=150){return Array.from({length:n},(_,i)=>({time:Date.UTC(2024,0,1)+i*DAY,open:100,high:101,low:99,close:100,volume:1000}));}

test('synthetic data and fingerprints are deterministic',()=>{
  const a=generateDemo(),b=generateDemo();
  assert.equal(a.fingerprint,b.fingerprint);assert.deepEqual(a.bars,b.bars);
  assert.equal(a.quality.rows,1461);assert.equal(a.quality.status,'PASSED');
});
test('CSV round trip retains exact OHLC values and UTC times',()=>{
  const demo=generateDemo();const imported=importDataset(barsToCSV(demo),{symbol:'TEST',source:'test'});
  assert.deepEqual(imported.bars,demo.bars);assert.equal(imported.quality.status,'PASSED');
});
test('malformed, duplicated, reversed, missing and invalid values are distinguished',()=>{
  const csv=barsToCSV(generateDemo()),lines=csv.split('\r\n'),meta={symbol:'T',source:'test'};
  assert.throws(()=>importDataset([lines[0],lines[1],...lines.slice(1)].join('\n'),meta),/重複/);
  assert.throws(()=>importDataset([lines[0],lines[2],lines[1],...lines.slice(3)].join('\n'),meta),/遞增/);
  assert.throws(()=>importDataset(csv.replace(lines[1],lines[1].replace(/,[^,]+$/,',NaN')),meta),/有效數字/);
  const missing=importDataset([lines[0],lines[1],...lines.slice(3)].join('\n'),meta);
  assert.equal(missing.quality.status,'REVIEW_REQUIRED');assert.equal(missing.quality.missing,1);
  assert.throws(()=>runResearch(missing,{}),/資料品質/);
  assert.throws(()=>parseTimestamp('2024-02-30'),/日期無效/);
  assert.throws(()=>parseTimestamp('2024-01-01T08:00:00+08:00'),/UTC/);
  assert.throws(()=>importDataset(csv.replace('timestamp,open','date,timestamp'),meta),/欄位/);
});
test('manual cash accounting includes two-sided commission, adverse prices and lot rounding',()=>{
  const bars=flatBars();bars.at(-1).close=110;bars.at(-1).high=111;
  const c=normalizeConfig({capital:1000,allocation:1,commissionBps:100,slippageBps:100,spreadBps:200,lotSize:1});
  const r=simulate(bars,c,{baseline:true});
  // Buy 9 @102 + 9.18 fee => cash 72.82. Sell 9 @107.8 - 9.702 fee.
  approx(r.nav.at(-1).nav,1033.318);approx(r.metrics.netPnl,33.318);
  approx(r.metrics.totalCommission,18.882);approx(r.reconciliationError,0);
  assert.equal(r.fills[0].quantity,9);assert.ok(r.nav.every(n=>n.cash>=0));
  assert.equal(r.fills.at(-1).reason,'END_OF_PERIOD');
});
test('no-trade paths retain all zero-return days and report N/A ratios',()=>{
  const c=normalizeConfig({fast:5,slow:20});const r=simulate(flatBars(),c);
  assert.equal(r.metrics.tradeCount,0);assert.equal(r.metrics.sharpe,null);assert.equal(r.metrics.calmar,null);
  assert.equal(r.metrics.winRate,null);assert.equal(r.metrics.profitFactor,null);
  assert.equal(r.metrics.zeroDays,150);assert.equal(r.nav.length,150);assert.equal(r.metrics.finalNav,c.capital);
});
for(const strategy of ['sma','breakout','rsi']){
  test(`${strategy}: future perturbations cannot alter past features, decisions, fills or NAV`,()=>{
    const bars=generateDemo().bars,c=normalizeConfig({strategy}),cut=600;
    const changed=bars.map((b,i)=>i<cut?{...b}:{...b,open:b.open*3,high:b.high*3,low:b.low*3,close:b.close*3});
    const a=simulate(bars,c,{liquidate:false}),b=simulate(changed,c,{liquidate:false});
    assert.deepEqual(features(bars,c).slice(0,cut),features(changed,c).slice(0,cut));
    assert.deepEqual(a.nav.slice(0,cut),b.nav.slice(0,cut));
    assert.deepEqual(a.decisions.slice(0,cut),b.decisions.slice(0,cut));
    assert.deepEqual(a.fills.filter(x=>x.index<cut),b.fills.filter(x=>x.index<cut));
    for(const f of a.fills){assert.ok(Date.parse(f.featureAvailableTime)<=Date.parse(f.time));assert.equal(f.sequence,'PREVIOUS_CLOSE_THEN_NEXT_OBSERVED_OPEN');}
  });
  test(`${strategy}: terminal NAV reconciles to independent sum of completed net trades`,()=>{
    const r=simulate(generateDemo().bars,normalizeConfig({strategy}));
    approx(r.metrics.netPnl,r.trades.reduce((s,t)=>s+t.netPnl,0),1e-6);
    approx(r.nav.at(-1).quantity,0);assert.ok(r.nav.every(n=>n.cash>=-1e-8));
  });
}
test('signals generated using close are filled only on next bar open',()=>{
  const bars=flatBars(),c=normalizeConfig({fast:2,slow:3,commissionBps:0,spreadBps:0,slippageBps:0});
  bars[2]={...bars[2],close:110,high:111};bars[3]={...bars[3],open:120,high:121,close:120};
  const r=simulate(bars,c);assert.equal(r.fills[0].index,3);assert.equal(r.fills[0].price,120);
  assert.equal(r.fills[0].decisionTime,new Date(bars[2].time+DAY).toISOString());
});
test('parameter selection never reads evaluation data; rejected candidates remain in ledger',()=>{
  const d=generateDemo(),c={mode:'sweep',strategy:'sma',minTrades:2};
  const a=runResearch(d,c),split=a.splitIndex;
  const modified={...d,bars:d.bars.map((b,i)=>i<split?b:{...b,open:b.open*2,high:b.high*2,low:b.low*2,close:b.close*2})};
  const b=runResearch(modified,c);
  assert.deepEqual(a.selected,b.selected);assert.deepEqual(a.trials,b.trials);assert.equal(a.winner,b.winner);
  assert.equal(a.trials.length,9);assert.ok(a.trials.every(t=>t.evidence==='DEVELOPMENT'));
  assert.equal(a.evaluation.nav.length,d.bars.length-split);assert.equal(a.baseline.nav.length,a.evaluation.nav.length);
  const {hash:stored,...contract}=a.contract;assert.equal(stored,hash(contract));
});
test('no eligible sweep winner produces explicit fixed fallback without promotion',()=>{
  const r=runResearch(generateDemo(),{mode:'sweep',minTrades:1000});
  assert.equal(r.winner,null);assert.equal(r.selected.fast,20);assert.equal(r.research_decision,'RESEARCH_ONLY');
  assert.equal(r.activation_state,'INACTIVE');assert.ok(r.reason_codes.includes('NO_ELIGIBLE_CANDIDATE_FIXED_FALLBACK'));
});
test('sample standard deviation and initial NAV high-water mark match hand calculations',()=>{
  const bars=flatBars();bars[0].close=90;bars[0].low=89;
  const c=normalizeConfig({capital:1000,allocation:1,commissionBps:0,slippageBps:0,spreadBps:0,lotSize:1});
  const r=simulate(bars,c,{baseline:true});approx(r.nav[0].drawdown,0.1);approx(r.metrics.maxDrawdown,0.1);
  const returns=r.nav.map(x=>x.return),m=returns.reduce((s,v)=>s+v,0)/returns.length;
  const variance=returns.reduce((s,v)=>s+(v-m)**2,0)/(returns.length-1);
  approx(r.metrics.sharpe,m/Math.sqrt(variance)*Math.sqrt(365));
});
test('complete month denominator excludes partial edges and includes inactive months',()=>{
  const r=simulate(flatBars(150),normalizeConfig({fast:5,slow:20}));
  assert.equal(r.metrics.completeMonths,4);assert.equal(r.metrics.positiveMonths,0);assert.equal(r.metrics.positiveMonthRatio,0);
  assert.equal(r.metrics.monthly.at(-1).complete,false);
});
test('invalid exposure, costs and strategy parameters fail before simulation',()=>{
  assert.throws(()=>normalizeConfig({allocation:1.1}),/資金配置/);
  assert.throws(()=>normalizeConfig({commissionBps:-1}),/手續費/);
  assert.throws(()=>normalizeConfig({fast:60,slow:20}),/短窗口/);
  assert.throws(()=>normalizeConfig({capital:'100000'}),/起始資金/);
  assert.throws(()=>normalizeConfig({strategy:'unknown'}),/未知策略/);
});
