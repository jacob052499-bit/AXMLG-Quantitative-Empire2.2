import copy,json,os,tempfile,unittest
from pathlib import Path
import worker,pandas as pd
from factor_scanner import rank_items
from test_research_tools import frame,dataset

def item():
    return dict(datasetId='ds-A',status='COMPLETED',selected=['momentum_short'],factorStatistics=[dict(name='momentum_short',direction=1,selectionStatus='SELECTED',p=.001,validationIC=.1)],readiness=dict(validationCalendarDays=180,validationDailyObservations=150,doubleCostValidationNetPnl=50,validationExcessReturn=.01),validation=dict(netPnl=100,totalReturn=.1,cagr=.2,sharpe=2,profitToMdd=2,maxDrawdown=.1,tradeCount=30),evaluation=dict(totalReturn=10))

class FactorScannerTests(unittest.TestCase):
    def test_full_planned_family_includes_failed_and_pending_assets(self):
        rows=[item(),dict(datasetId='ds-B',status='FAILED',error='missing')];rule=rank_items(rows,['momentum_short','volume_price'],.1,2)
        self.assertEqual(rule['familySize'],4);self.assertAlmostEqual(rows[0]['globalQ'],.001*4*(1+1/2+1/3+1/4));self.assertEqual(rows[1]['score'],None)
        self.assertEqual(rows[0]['recommendation'],'可優先進行後續研究');self.assertGreater(rows[0]['score'],90)
    def test_final_test_results_never_change_score_or_recommendation(self):
        a=[item()];b=copy.deepcopy(a);b[0]['evaluation']=dict(totalReturn=-100,sharpe=-100,maxDrawdown=1)
        rank_items(a,['momentum_short'],.1,1);rank_items(b,['momentum_short'],.1,1)
        for k in ['score','components','recommendation','reasons']:self.assertEqual(a[0][k],b[0][k])
    def test_evidence_failure_and_negative_validation_cap_research_priority(self):
        a=item();a['validation']['netPnl']=-100;rank_items([a],['momentum_short'],.1,1)
        self.assertLessEqual(a['score'],39);self.assertEqual(a['scoreCeiling'],39);self.assertTrue(any('淨利' in x for x in a['reasons']))
        b=item();b['factorStatistics'][0]['p']=.2;rank_items([b],['momentum_short'],.1,1);self.assertEqual(b['score'],0)
        c=item();c['selected']=[];rank_items([c],['momentum_short'],.1,1);self.assertEqual(c['score'],0)
        d=item();d['readiness']['validationCalendarDays']=3;rank_items([d],['momentum_short'],.1,1);self.assertLessEqual(d['score'],59);self.assertTrue(any('90' in x for x in d['reasons']))
        e=item();e['readiness']['doubleCostValidationNetPnl']=-1;rank_items([e],['momentum_short'],.1,1);self.assertLessEqual(e['score'],59)
    def test_scan_different_symbols_currencies_periods_keeps_failed_item_and_continues(self):
        datasets=[]
        for i,(symbol,currency,timeframe,n) in enumerate([('XAUUSD','USD','1 Hour',2400),('TOO_SHORT','USD','1 Hour',200),('BTCJPY','JPY','5 Min',2400)]):
            d=dataset(frame(n,i),symbol);d['name']=symbol;d['identity'].update(source='Synthetic scanner engineering fixture',currency=currency,timeframe=timeframe);d['quality'].update(start='2025-01-01',end='2025-05-01');datasets.append(d)
        f=frame(2400,8);f.index=pd.bdate_range('2015-01-01',periods=2400,tz='UTC');daily=dataset(f,'STOCK-DAILY');daily['name']='STOCK-DAILY';daily['identity'].update(timeframe='1 Day',source='Synthetic business-day fixture');daily['quality'].update(status='REVIEW_REQUIRED',duplicate=0,invalid=0,missing=900);datasets.append(daily)
        request=dict(action='scan',datasets=datasets,config=dict(horizon=5),tools=dict(factors=['momentum_short','trend_gap']))
        previous=os.getcwd()
        with tempfile.TemporaryDirectory(prefix='qe-scan-test-') as folder:
            try:
                os.chdir(folder);r=worker.run(request);saved=json.loads(Path('scan-progress.json').read_text(encoding='utf-8'))
                self.assertEqual([x['status'] for x in r['items']],['COMPLETED','FAILED','COMPLETED','COMPLETED']);self.assertEqual(r['ranking']['familySize'],8);self.assertEqual(r['completed'],3);self.assertEqual(saved['status'],'COMPLETED_WITH_EXCLUSIONS')
                self.assertEqual(r['items'][2]['currency'],'JPY');self.assertEqual(r['items'][2]['timeframe'],'5 Min');self.assertTrue(Path(r['items'][2]['recordFile']).exists())
                self.assertEqual(r['items'][3]['sourceQuality'][0]['calendarPolicy'],'OBSERVED_DAILY_REVIEW_REQUIRED');self.assertEqual(daily['quality']['status'],'REVIEW_REQUIRED')
            finally:os.chdir(previous)

if __name__=='__main__':unittest.main()
