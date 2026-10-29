import copy
import unittest
import numpy as np
import pandas as pd
import worker
import factors
import time_rules


def frame(n=420,seed=4):
    rng=np.random.default_rng(seed);p=100*np.exp(np.cumsum(rng.normal(.0003,.012,n)));idx=pd.date_range('2025-01-01',periods=n,freq='h',tz='UTC')
    return pd.DataFrame(dict(open=p,close=p*(1+.002*np.sin(np.arange(n))),high=p*1.01,low=p*.99,volume=100+rng.integers(1,100,n)),index=idx)


def dataset(f,symbol='ASSET-A'):
    return dict(id='ds-'+symbol,identity=dict(symbol=symbol,timeframe='1 Hour',currency='USD',synthetic=True),quality=dict(status='PASSED'),bars=[dict(time=int(t.timestamp()*1000),**r) for t,r in f.to_dict('index').items()])


def payload(datasets,mode='single'):
    return dict(action='factor',datasets=datasets,config=dict(capital=100000,split=.65,horizon=5,selectedFactors=3,rebalanceBars=12,topAssets=2,commissionBps=5,slippageBps=3,spreadBps=2),tools=dict(mode=mode,factors=list(factors.NAMES)))


class ResearchToolsTests(unittest.TestCase):
    def test_dst_boundary_and_overnight_weekday(self):
        c=dict(tradingTimezone='America/New_York',sessionEnabled=True,sessionStart='09:30',sessionEnd='16:00',sessionDays=list(range(5)),barHours=1);time_rules.validate(c)
        dates=pd.to_datetime(['2026-03-06T14:00Z','2026-03-06T15:00Z','2026-03-09T13:00Z','2026-03-09T14:00Z','2026-03-09T20:00Z'],utc=True)
        self.assertEqual(time_rules.allowed(dates,c).tolist(),[False,True,False,True,False])
        c.update(sessionStart='22:00',sessionEnd='02:00',tradingTimezone='UTC',sessionDays=[4])
        self.assertEqual(time_rules.allowed(pd.to_datetime(['2026-03-06T23:00Z','2026-03-07T01:00Z','2026-03-07T02:00Z','2026-03-07T23:00Z'],utc=True),c).tolist(),[True,True,False,False])

    def test_session_requires_decision_and_execution_but_exit_unrestricted(self):
        f=frame(24);f.index=pd.date_range('2026-03-09',periods=24,freq='h',tz='UTC')
        c=dict(capital=100000,allocation=.95,commissionBps=0,slippageBps=0,spreadBps=0,lotSize=.000001,barHours=1,tradingTimezone='America/New_York',sessionEnabled=True,sessionStart='09:30',sessionEnd='16:00',sessionDays=[0])
        signal=np.ones(24);signal[20:]=0
        r=worker.backtest(f,signal,c)
        self.assertEqual(r['fills'][0]['time'],'2026-03-09T14:00:00+00:00')
        self.assertEqual(r['fills'][1]['time'],'2026-03-09T21:00:00+00:00')
        # A delayed entry would arrive outside the session and must remain unfilled.
        signal[:]=0;signal[18]=1
        self.assertEqual(len(worker.backtest(f,signal,c,delay=3)['fills']),0)

    def test_local_daily_grouping_keeps_total_pnl(self):
        rows=[dict(date=t,nav=v,quantity=0,cash=v,commission=0,executionDrag=0,hadPosition=False) for t,v in [('2026-03-09T23:00Z',110),('2026-03-10T00:00Z',120),('2026-03-10T16:00Z',130)]]
        result=worker.pyramiding.daily_nav(rows,100,timezone='Asia/Taipei')
        self.assertEqual([r['date'] for r in result],['2026-03-10','2026-03-11'])
        self.assertEqual(result[0]['nav'],120);self.assertEqual(sum(r['pnl'] for r in result),30)

    def test_rank_ic_and_frozen_group_edges(self):
        self.assertAlmostEqual(factors.rank_ic([1,2,3,4],[4,3,2,1]),-1)
        train,edges=factors.stats(pd.Series(range(100)),pd.Series(range(100)))
        test,_=factors.stats(pd.Series(range(100,120)),pd.Series(range(20)),edges)
        self.assertEqual(test['groups'][-1]['n'],20);self.assertEqual(test['groups'][0]['n'],0)

    def test_feature_prefix_and_oos_selection_are_invariant(self):
        f=frame();p=payload([dataset(f)]);a=worker.run(p)
        changed=f.copy();split=int(len(f)*.65);changed.iloc[split:,changed.columns.get_indexer(['open','high','low','close'])]*=2
        b=worker.run(payload([dataset(changed)]))
        self.assertEqual(a['selected'],b['selected']);self.assertEqual(a['causality']['status'],'PASS_PREFIX')
        for x,y in zip(a['factors'],b['factors']):self.assertEqual(x['development'],y['development']);self.assertEqual(x['direction'],y['direction'])
        self.assertEqual(a['quality']['purgedISRows'],6)
        self.assertAlmostEqual(a['evaluation']['reconciliationError'],0,places=7)

    def test_known_flat_portfolio_costs_and_accounting(self):
        f=frame(8);f.loc[:,'open']=100.;f.loc[:,'close']=100.;s=dict(capital=1000.,commissionBps=100.,slippageBps=0.,spreadBps=0.,barHours=1,tradingTimezone='UTC',rebalanceBars=100,topAssets=2)
        r=factors.portfolio([f,f.copy(),f.copy()],np.tile([3,2,1],(8,1)),['A','B','C'],s,1,worker.metrics)
        # 95% position after entry fee: E=1000/(1+.95*.01), then charge exit fee too.
        post=1000/1.0095;expected=post-post*.95*.01
        self.assertAlmostEqual(r['metrics']['finalNav'],expected,places=8)
        self.assertAlmostEqual(r['metrics']['netPnl'],-r['metrics']['totalCost'],places=8)
        self.assertEqual(len(r['fills']),4);self.assertGreaterEqual(min(x['cash'] for x in r['nav']),0)
        self.assertEqual(r['fills'][0]['decisionTime'],r['fills'][0]['time'])
        split=factors.portfolio([f,f.copy(),f.copy()],np.tile([3,2,1],(8,1)),['A','B','C'],{**s,'commissionBps':50.,'slippageBps':50.},1,worker.metrics)
        self.assertAlmostEqual(split['metrics']['finalNav'],expected,places=8)
        self.assertAlmostEqual(split['metrics']['totalCommission'],split['metrics']['totalCost']/2,places=8)
        self.assertAlmostEqual(split['metrics']['totalExecutionDrag'],split['metrics']['totalCost']/2,places=8)

    def test_portfolio_oos_weights_do_not_use_future_prices(self):
        fs=[frame(420,i) for i in range(3)];p=payload([dataset(f,'A'+str(i)) for i,f in enumerate(fs)],'portfolio');a=worker.run(p)
        changed=copy.deepcopy(p)
        for d in changed['datasets']:
            for bar in d['bars'][350:]:
                for k in ['open','high','low','close']:bar[k]*=1.7
        b=worker.run(changed)
        for x,y in zip(a['evaluation']['rebalances'],b['evaluation']['rebalances']):
            if x['time']<'2025-01-15T14:00:00+00:00':self.assertEqual(x,y)
        self.assertEqual(a['selected'],b['selected']);self.assertLess(abs(a['evaluation']['reconciliationError']),1e-7)

    def test_reject_duplicate_mixed_or_insufficient_universe(self):
        d=dataset(frame());p=payload([d],'portfolio')
        with self.assertRaisesRegex(ValueError,'至少 3'):worker.run(p)
        p=payload([d,d,copy.deepcopy(d)],'portfolio')
        with self.assertRaisesRegex(ValueError,'同一商品'):worker.run(p)
        ds=[dataset(frame(420,i),'A'+str(i)) for i in range(3)];ds[1]['identity']['currency']='EUR'
        with self.assertRaisesRegex(ValueError,'幣別'):worker.run(payload(ds,'portfolio'))


if __name__=='__main__':unittest.main()
