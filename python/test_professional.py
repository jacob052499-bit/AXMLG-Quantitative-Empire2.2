import contextlib,copy,io,itertools,json,os,tempfile,unittest
from pathlib import Path
import numpy as np
import pandas as pd
import worker
from parameter_search import run as search,selection_bias
from factor_evidence import evidence,equal_mdd_excess,python_draft
from test_research_tools import frame

class ProfessionalTests(unittest.TestCase):
    def test_real_budget_and_neighbor_lookup_match_independent_exhaustive_calculation(self):
        self.assertEqual(len(worker.candidate_list({},dict(a=list(range(101)),b=list(range(101))))),10201)
        with self.assertRaises(ValueError):worker.candidate_list({},dict(a=list(range(400)),b=list(range(400))))
        grid=dict(a=[1,4,10,30],b=[.1,.5,2]);trials=[]
        for i,(a,b) in enumerate(itertools.product(grid['a'],grid['b'])):trials.append(dict(trialId=str(i),params=dict(a=a,b=b),metrics=dict(sharpe=float(i)/10),eligible=i%3!=0))
        pp=worker.plateau(trials,grid)
        for cell in pp['cells']:
            actual=cell['neighborhood'];near=[t for t in trials if abs(grid['a'].index(t['params']['a'])-grid['a'].index(cell['params']['a']))<=1 and abs(grid['b'].index(t['params']['b'])-grid['b'].index(cell['params']['b']))<=1];ok=[t['metrics']['sharpe'] for t in near if t['eligible']]
            self.assertEqual(actual['total'],len(near));self.assertEqual(actual['n'],len(ok));self.assertAlmostEqual(actual['medianSharpe'],float(np.median(ok)))
    def test_checkpoint_resume_does_not_repeat_completed_candidates_or_accept_changed_data(self):
        f=frame(100);c=dict(capital=1000.,allocation=.9,commissionBps=0.,slippageBps=0.,spreadBps=0.,lotSize=.001,minTrades=1,maxDrawdown=.8,barHours=1,calendarVerified=False);code='def strategy(df,params):\n    return pd.Series(np.arange(len(df))%params["period"]<2,index=df.index)';before=os.getcwd()
        with tempfile.TemporaryDirectory(prefix='qe-search-proof-') as folder:
            try:
                os.chdir(folder)
                with contextlib.redirect_stdout(io.StringIO()):a,first=search(code,f,c,{},dict(period=[3,4,5]),0,80,checkpoint=True);b,resumed=search(code,f,c,{},dict(period=[3,4,5]),0,80,checkpoint=True)
                self.assertEqual(a,b);self.assertEqual(first['resumed'],0);self.assertEqual(resumed['resumed'],3);self.assertEqual(len(Path('search-0-80.ndjson').read_text(encoding='utf-8').splitlines()),3)
                changed=f.copy();changed.iloc[2,0]*=1.1
                with self.assertRaisesRegex(ValueError,'契約不符'):search(code,changed,c,{},dict(period=[3,4,5]),0,80,checkpoint=True)
            finally:os.chdir(before)
    def test_fold_directions_use_past_only_and_bootstrap_is_reproducible(self):
        a=np.r_[np.linspace(.1,.2,30),np.linspace(-.2,-.1,30),np.linspace(.1,.2,60)];r=evidence(a,1);self.assertEqual(r,evidence(a,1));self.assertEqual(r['folds'][0]['pastDirection'],1);self.assertFalse(r['folds'][0]['positive']);self.assertGreater(r['ci95'][1],r['ci95'][0]);self.assertIsNone(evidence([.1]*5,1)['ci95'])
    def test_equal_mdd_formula_matches_manual_money_arithmetic(self):
        def section(v):return dict(nav=[dict(date=str(i),nav=x) for i,x in enumerate(v)])
        r=equal_mdd_excess(section([100,120,110,140]),section([100,130,110,150]),100)
        self.assertEqual(r['targetMdd'],10.);self.assertEqual(r['benchmarkMultiplier'],.5);self.assertEqual(r['excessPnl'],15.)
    def test_exported_logic_refuses_earlier_training_and_only_signals_after_freeze(self):
        f=frame(400);cut=f.index[249].isoformat();plan=dict(mode='single',components=[dict(name='momentum_short',direction=1,weight=1.)],freezeTime=cut,trainingFeatureEnd=f.index[243].isoformat());s=dict(shortWindow=5,mediumWindow=20,longWindow=60,winsorQuantile=.01,scoreThreshold=.6,exitThreshold=.5,capital=1000.,commissionBps=0.,slippageBps=0.,spreadBps=0.,allocation=.95,trainFraction=.6,embargoBars=5,lotSize=.001,barHours=1)
        draft=python_draft(plan,s,{});scope=worker.namespace(draft['source'],f,{})
        with self.assertRaisesRegex(ValueError,'拒絕前視'):scope['fit'](f.iloc[:200],{})
        model=scope['fit'](f.iloc[:260],{});signals=scope['strategy'](f,{},model);self.assertTrue((signals.iloc[:250]==0).all());changed=f.copy();changed.iloc[350:,0:4]*=1.5
        other=scope['strategy'](changed,{},model);np.testing.assert_array_equal(signals.iloc[:350],other.iloc[:350])

if __name__=='__main__':unittest.main()
