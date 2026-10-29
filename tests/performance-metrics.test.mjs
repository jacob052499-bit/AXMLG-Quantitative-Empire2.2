import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {tradeStatistics,performanceMetrics,performanceTable} from '../lib/performance-metrics.mjs';
import {uiRun} from '../lib/research-tools.mjs';
import {runResearch} from '../lib/engine.mjs';
import {generateDemo} from '../lib/data.mjs';
import {researchReport} from '../lib/report.mjs';

test('Exported MC report preserves monetary drawdown quantiles and interactive conditional distributions',()=>{
 const r=runResearch(generateDemo(),{strategy:'sma',mode:'single'},{id:'run-mc-report',programHash:'test'}),q={p05:1,p50:2,p95:3};
 r.analysis={plateau:{definition:'fixed'},causality:{status:'NOT_RUN',limitation:'test'},monteCarlo:{protocolVersion:'CONDITIONAL_MC_V2',scope:'Conditional daily returns only',method:'stationary',seed:42,simulations:100,blockSize:5,terminalReturn:q,maxDrawdown:q,maxDrawdownAmount:{p05:100,p50:500,p95:1234.5},sharpe:q,observations:100,equityFloor:.7,floorBreachFraction:0,fan:[{bar:0,p05:1000,p50:1000,p95:1000},{bar:100,p05:900,p50:1100,p95:1300}],histograms:{maxDrawdownAmount:[{low:0,high:1000,count:100}],terminalReturn:[{low:0,high:1,count:100}]}}};
 const html=researchReport(r);assert.match(html,/<td>MDD 金額 \(USD\)<\/td><td>100\.000<\/td><td>500\.000<\/td><td>1234\.500<\/td>/);assert.match(html,/MC 最大回撤金額分布/);assert.match(html,/data-qchart=/);assert.match(html,/不代表券商爆倉機率/);assert.ok(!html.includes('<td>MDD</td>'));
});
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-10,`${a} != ${b}`);
const trades=pnls=>pnls.map((netPnl,i)=>({entryTime:'2024-01-01',exitTime:'2024-01-'+String(i+2).padStart(2,'0'),netPnl,barsHeld:i+1}));
function section(){return {nav:[{date:'2024-01-01',nav:110,return:.1,pnl:10,drawdown:0,hadPosition:true},{date:'2024-01-02',nav:99,return:-.1,pnl:-11,drawdown:.1,hadPosition:true},{date:'2024-01-03',nav:99,return:0,pnl:0,drawdown:.1,hadPosition:false}],trades:trades([20,-5,0]),cycles:trades([10]),metrics:{initialNav:100,finalNav:99,netPnl:-1,totalReturn:-.01,maxDrawdown:.1,maxDrawdownAmount:11,cagr:-.7,calmar:-7,annualization:365,riskFreeRate:0}};}
test('Trade metrics reconcile net expectancy, PF, average win/loss, flats and streaks by exit order',()=>{
 const rows=trades([90,-40,0,30,-20]),p=tradeStatistics(rows.slice().reverse());
 near(p.winRate,.4);near(p.profitFactor,2);near(p.expectancy,12);near(p.averageWin,60);near(p.averageLoss,30);near(p.payoffRatio,2);near(p.breakEvenWinRate,1/3);assert.equal(p.netPnl,60);assert.equal(p.medianPnl,0);assert.equal(p.flat,1);assert.equal(p.longestWinStreak,1);assert.equal(p.longestLossStreak,1);
 const s=tradeStatistics(trades([5,10,-1,-2,0,-3]));assert.equal(s.longestWinStreak,2);assert.equal(s.longestLossStreak,2);
});
test('Empty, zero, all winning and all losing trade metrics distinguish N/A, infinity and zero PF',()=>{
 assert.equal(tradeStatistics([]).winRate,null);assert.equal(tradeStatistics([]).expectancy,null);assert.equal(tradeStatistics([]).profitFactor,null);
 assert.equal(tradeStatistics(trades([0,0])).profitFactor,null);assert.equal(tradeStatistics(trades([0,0])).winRate,0);
 assert.equal(tradeStatistics(trades([1,2,0])).profitFactor,'Infinity');assert.equal(tradeStatistics(trades([-2,-1])).profitFactor,0);assert.equal(tradeStatistics(trades([-2,-1])).payoffRatio,null);
 assert.throws(()=>tradeStatistics([{netPnl:NaN}]),/無效/);
});
test('Sharpe uses sample daily deviation and zero days; intrabar MDD, closed legs and completed cycles remain distinct',()=>{
 const s=section();s.metrics.tvMaxDrawdown=.2;s.metrics.tvMaxDrawdownAmount=22;s.metrics.openPnl=-16;const before=JSON.stringify(s),p=performanceMetrics(s);
 near(p.risk.sharpe,0);near(p.risk.sortino,0);assert.equal(p.period.dailyObservations,3);assert.equal(p.trading.legs.netPnl,15);assert.equal(p.trading.cycles.netPnl,10);near(p.risk.netProfitToMdd,15/22);assert.equal(p.risk.maxDrawdown,.2);assert.equal(p.risk.closeMaxDrawdown,.1);assert.equal(p.risk.longestDrawdownDays,2);assert.equal(p.risk.unrecoveredDrawdownDays,2);near(p.risk.exposure,2/3);assert.equal(JSON.stringify(s),before);
 s.nav[0].return=.2;const expectedMean=.1/3,expectedSd=Math.sqrt(((.2-expectedMean)**2+(-.1-expectedMean)**2+(0-expectedMean)**2)/2);near(performanceMetrics(s).risk.sharpe,expectedMean/expectedSd*Math.sqrt(365));
 s.metrics.insolvent=true;assert.equal(performanceMetrics(s).risk.sharpe,null);assert.equal(performanceMetrics(s).risk.sortino,null);
});
test('Server derives full report before UI truncation and refuses statistics from sampled data',()=>{
 const s=section();s.trades=trades(Array.from({length:700},(_,i)=>i<600?2:-1));const r={selected:{capital:100},evaluation:s},before=JSON.stringify(r),view=uiRun(r);
 assert.equal(view.evaluation.trades.length,500);assert.equal(view.evaluation.reportMetrics.trading.legs.count,700);near(view.evaluation.reportMetrics.trading.legs.expectancy,1100/700);assert.equal(view.evaluation.reportMetrics.trading.legs.profitFactor,12);assert.equal(JSON.stringify(r),before);assert.throws(()=>performanceMetrics(view.evaluation),/完整帳本/);
});
test('Offline table and UI render all core metrics, explicit infinity, definitions and selectable trade units',async()=>{
 const p=performanceMetrics(section()),table=performanceTable(p);for(const term of ['勝率','PF 值','夏普','每筆期望值','MDD','Sortino','完整績效指標'])assert.ok(table.includes(term),term);
 const ctx=vm.createContext({fmt:(v,n=2)=>Number(v).toFixed(n),esc:v=>String(v??'').replaceAll('<','&lt;')});vm.runInContext(await readFile(new URL('../app/report-metrics.js',import.meta.url),'utf8'),ctx);
 const output=ctx.renderPerformanceSummary(p,'legs');for(const key of ['winRate','profitFactor','sharpe','expectancy','maxDrawdown'])assert.ok(output.includes(`data-metric="${key}"`));assert.ok(output.includes('data-report-unit="cycles"'));const mdd=output.match(/<article[^>]*data-metric="maxDrawdown"[\s\S]*?<\/article>/)[0];assert.ok(mdd.includes('11.00'));assert.ok(!mdd.includes('10.00%'));
 p.trading.legs.profitFactor='Infinity';assert.ok(ctx.renderPerformanceSummary(p).includes('∞'));p.trading.legs=tradeStatistics([]);assert.ok(ctx.renderPerformanceSummary(p).includes('N/A'));assert.ok(!ctx.renderPerformanceSummary(p).includes('NaN'));
});
