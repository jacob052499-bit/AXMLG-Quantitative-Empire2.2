import unittest,copy,tempfile,os
from pathlib import Path
import numpy as np
import pandas as pd
import worker
from factors import rank_ic
from factor_statistics import block_rank_ic
from risk_simulation import simulate
from batch_research import run_batch
from test_research_tools import frame,dataset

class UpgradeTests(unittest.TestCase):
    def test_vector_blocks_match_slow_spearman_with_ties_missing_and_tail(self):
        rng=np.random.default_rng(16);a=rng.integers(0,9,937).astype(float);b=rng.normal(size=937);a[::13]=np.nan;b[::17]=np.nan
        for size in [30,60,100,500]:
            got,counts=block_rank_ic(a,b,size)
            for j,v in enumerate(got):
                aa=a[j*size:(j+1)*size];bb=b[j*size:(j+1)*size];mask=np.isfinite(aa)&np.isfinite(bb)
                expected=rank_ic(aa,bb) if mask.sum()>=max(20,size//2) else None
                if expected is None:self.assertTrue(np.isnan(v))
                else:self.assertAlmostEqual(v,expected,13)
                self.assertEqual(counts[j],mask.sum())
        self.assertTrue(np.isnan(block_rank_ic(np.ones(60),np.arange(60),60)[0][0]))
    def test_mc_flat_and_all_loss_manual_money_drawdown_floor_and_seed(self):
        nav=[{'return':-.01} for _ in range(100)];s=dict(method='stationary',simulations=100,blockSize=5,seed=17,equityFloor=.7)
        r=simulate(nav,1000,s);expected=1000*(1-.99**100)
        for k in ['p05','p50','p95']:self.assertAlmostEqual(r['maxDrawdownAmount'][k],expected,9)
        self.assertEqual(r['floorBreachFraction'],1);self.assertEqual(r['maxUnderwaterDays']['p95'],100)
        self.assertEqual(r,simulate(nav,1000,s));flat=simulate([{'return':0}]*100,1000,s);self.assertEqual(flat['maxDrawdownAmount']['p95'],0);self.assertEqual(flat['floorBreachFraction'],0)
    def test_shuffle_preserves_total_but_changes_drawdown(self):
        nav=[{'return':v} for v in [.05,-.04,.01,-.03]*25];r=simulate(nav,1000,dict(method='shuffle',simulations=100,blockSize=5,seed=9))
        self.assertAlmostEqual(r['terminalReturn']['p05'],r['terminalReturn']['p95'],12);self.assertLess(r['maxDrawdownAmount']['p05'],r['maxDrawdownAmount']['p95'])
    def test_all_bad_parameters_remain_cash_and_retain_failures(self):
        p=dict(action='research',dataset=dataset(frame(500)),source='def strategy(df, params):\n    raise ValueError("bad parameter")',config=dict(capital=1000,split=.65),params={},grid=dict(x=[1,2],y=[3,4]),tools=dict(stress=False,mc=dict(enabled=False)))
        r=worker.run(p);self.assertIsNone(r['winner']);self.assertEqual(len(r['trials']),4);self.assertTrue(all(t['status']=='FAILED' for t in r['trials']));self.assertEqual(r['evaluation']['metrics']['netPnl'],0);self.assertEqual(r['evaluation']['fills'],[])
        f=frame(1500);f['open']=np.linspace(100,300,len(f));f['close']=f.open;f['high']=f.open*1.01;f['low']=f.open*.99
        p.update(dataset=dataset(f),source='def strategy(df, params):\n    return pd.Series((np.arange(len(df))//int(params["x"]))%2, index=df.index)',grid=dict(x=[3,4],y=[1,2]),config=dict(capital=1000,split=.65,commissionBps=0,slippageBps=0,spreadBps=0),tools=dict(selector='plateau',stress=False,mc=dict(enabled=False)))
        good=worker.run(p);self.assertIsNotNone(good['winner']);chosen=next(t for t in good['trials'] if t['trialId']==good['winner']);self.assertEqual(chosen['neighborhood']['n'],4);self.assertGreater(chosen['metrics']['netPnl'],0)
    def test_batch_keeps_failures_and_native_periods_without_pooling_currency(self):
        old=Path.cwd()
        with tempfile.TemporaryDirectory(prefix='qe-batch-unit-') as folder:
            try:
                os.chdir(folder);a=dataset(frame(500),'A');a['name']='A';b=copy.deepcopy(a);b['id']='ds-B';b['identity']['symbol']='B';b['identity']['currency']='EUR'
                def runner(p):
                    self.assertNotIn('timeframeMinutes',p['config'])
                    if p['dataset']['id']=='ds-B':raise ValueError('invalid quote')
                    return dict(evaluation=dict(metrics=dict(netPnl=12)),parameters=p['params'],config=p['config'])
                r=run_batch(dict(datasets=[a,b],source='test',config=dict(timeframeMinutes=60),params=dict(x=3),tools=dict(nativePeriods=True)),runner,worker.clean)
                self.assertEqual((r['completed'],r['failed']),(1,1));self.assertEqual(r['scope'],'INDEPENDENT_ACCOUNTS_NOT_PORTFOLIO');self.assertNotIn('metrics',r);self.assertEqual(r['items'][1]['currency'],'EUR');self.assertIn('invalid quote',r['items'][1]['error'])
            finally:os.chdir(old)
if __name__=='__main__':unittest.main()
