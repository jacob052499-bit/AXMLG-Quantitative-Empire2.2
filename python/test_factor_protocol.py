import copy,math,unittest
import numpy as np
import pandas as pd
import worker
from factor_statistics import hac_mean,adjust_fdr,ecdf_score,segment_stats
from factor_portfolio import simulate,capped_weights
from factor_research import settings
from factors import rank_ic
from test_research_tools import frame,dataset

def request(fs,mode='single',**config):
    return dict(action='factor',datasets=[dataset(f,'TEST-'+str(i)) for i,f in enumerate(fs)],config=dict(capital=1000.,horizon=5,fdrMethod='none',**config),tools=dict(mode=mode,protocol='robust_v2',factors=['momentum_short','trend_gap']))

class FactorProtocolTests(unittest.TestCase):
    def test_hac_bartlett_matches_independent_arithmetic(self):
        a=np.array([.2,-.1,.1,-.2]*8);r=hac_mean(a,lags=2)
        avg=sum(a)/len(a);center=[x-avg for x in a];g0=sum(x*x for x in center)/len(a)
        lrv=g0+2*sum((1-k/3)*sum(center[i]*center[i-k] for i in range(k,len(a)))/len(a) for k in (1,2))
        self.assertAlmostEqual(r['se'],math.sqrt(lrv/len(a)));self.assertAlmostEqual(r['p'],1.)
        self.assertEqual(hac_mean([.2]*30)['status'],'DEGENERATE_VARIANCE');self.assertIsNone(hac_mean([1]*10)['p'])
    def test_fdr_family_includes_unavailable_tests_and_by_dependency_penalty(self):
        bh=adjust_fdr([.01,.04,.03,None],'BH');by=adjust_fdr([.01,.04,.03,None],'BY')
        np.testing.assert_allclose(bh,[.04,.05333333333333333,.05333333333333333,1])
        np.testing.assert_allclose(by[:3],np.array(bh[:3])*sum(1/x for x in range(1,5)))
        self.assertEqual(adjust_fdr([None,None]),[1,1])
    def test_frozen_ecdf_and_cross_section_quantiles(self):
        score=ecdf_score([1,2,3,4],[1,2,4,100,np.nan],0);np.testing.assert_allclose(score[:4],[.125,.375,.875,1]);self.assertTrue(np.isnan(score[-1]))
        idx=pd.date_range('2025-01-01',periods=40,freq='h',tz='UTC');x=pd.DataFrame(np.tile(np.arange(5),(40,1)),index=idx);y=x*.01
        s,_=segment_stats(x,y,'portfolio',rank_ic,0,40,1)
        self.assertEqual([g['n'] for g in s['groups']],[40]*5);self.assertAlmostEqual(s['spread'],.04);self.assertAlmostEqual(s['ic'],1.)
    def test_post_cost_caps_fifo_and_cash_match_manual_flat_price_costs(self):
        f=frame(8);f.loc[:,'open']=100.;f.loc[:,'close']=100.;score=np.ones((8,1));s=settings({'config':dict(capital=1000.,commissionBps=100.,slippageBps=0.,spreadBps=0.)});s.update(barHours=1,calendarVerified=False)
        r=simulate([f],score,['A'],s,1,8,worker.metrics);post=1000/(1+.95*.01);expected=post*(1-.95*.01)
        self.assertAlmostEqual(r['metrics']['finalNav'],expected,places=8);self.assertAlmostEqual(sum(t['netPnl'] for t in r['trades']),expected-1000,places=8);self.assertEqual(r['executionAudit']['status'],'PASS');self.assertGreaterEqual(min(n['cash'] for n in r['nav']),0)
        np.testing.assert_allclose(capped_weights([1,1,0],.95,.35),[.35,.35,0])
        r=simulate([f]*5,np.tile([5,4,3,2,1],(8,1)),list('ABCDE'),{**s,'topAssets':2},1,8,worker.metrics)
        self.assertTrue(all(w<=.35+1e-12 for b in r['rebalances'] for w in b['weights'].values()));self.assertLess(abs(r['reconciliationError']),1e-7)
    def test_no_passing_factor_stays_cash_without_fallback(self):
        f=frame(1500);f['volume']=0;p=request([f]);p['tools']['factors']=['volume_surprise','volume_price'];r=worker.run(p)
        self.assertEqual(r['selected'],[]);self.assertEqual(r['protocol']['status'],'NO_FACTOR_PASSED_CASH');self.assertEqual(r['evaluation']['metrics']['finalNav'],1000.);self.assertEqual(r['evaluation']['fills'],[]);self.assertEqual(r['protocol']['familySize'],2)
    def test_future_prices_cannot_change_training_selection_or_earlier_orders(self):
        p=request([frame(3000,7)]);a=worker.run(p);changed=copy.deepcopy(p);cut=2750
        for b in changed['datasets'][0]['bars'][cut:]:
            for k in ['open','high','low','close']:b[k]*=1.7
        b=worker.run(changed);self.assertEqual(a['selected'],b['selected']);self.assertEqual(a['correlation'],b['correlation']);self.assertEqual([r['development'] for r in a['factors']],[r['development'] for r in b['factors']])
        time=pd.to_datetime(changed['datasets'][0]['bars'][cut]['time'],unit='ms',utc=True).isoformat()
        self.assertEqual([x for x in a['evaluation']['fills'] if x['time']<time],[x for x in b['evaluation']['fills'] if x['time']<time]);self.assertLess(abs(a['evaluation']['reconciliationError']),1e-7)
        plan=a['protocol']['splitIndices'];self.assertLess(plan['trainEnd']+p['config']['horizon']+1,plan['validationStart']);self.assertLess(plan['validationEnd']+p['config']['horizon']+1,plan['testStart'])
    def test_cross_section_gate_and_deterministic_portfolio_protocol(self):
        with self.assertRaisesRegex(ValueError,'至少需要 5'):worker.run(request([frame(1500,i) for i in range(3)],'portfolio'))
        r=worker.run(request([frame(1500,i) for i in range(5)],'portfolio'));self.assertEqual(r['protocolVersion'],'FACTOR_RESEARCH_V4');self.assertEqual(r['causality']['status'],'PASS_PREFIX');self.assertEqual(r['evaluation']['executionAudit']['status'],'PASS');self.assertGreater(len(r['baseline']['fills']),0)
        self.assertTrue(all(v<=.35+1e-9 for b in r['baseline']['rebalances'] for v in b['weights'].values()))
if __name__=='__main__':unittest.main()
