import unittest
import numpy as np
import pandas as pd
import worker
import execution

C=dict(capital=1000.,allocation=.95,commissionBps=0.,slippageBps=0.,spreadBps=0.,lotSize=.1,pointValue=1.,marginRate=.1,barHours=1,calendarVerified=False,priceSide='BID',executionMode='ohlc_conservative')
def frame(rows):
    return pd.DataFrame([dict(open=o,high=h,low=l,close=c,volume=0.) for o,h,l,c in rows],index=pd.date_range('2024-01-01',periods=len(rows),freq='h',tz='UTC'))
def plan(df,q=1,add=False):
    def callback(bar,position,state,params):
        if bar['n']==0 or add and bar['n']==1:return [dict(type='entry',qty=q,id='buy')]
        return []
    return dict(features=pd.DataFrame(dict(n=np.arange(len(df))),index=df.index),on_bar=callback,params={},meta={'pyramiding':100})
def run(df,**config):return worker.backtest(df,plan(df),{**C,**config})

class ExecutionTests(unittest.TestCase):
    def test_ambiguous_bar_uses_stop_first_with_interval_timestamp(self):
        df=frame([(100,100,100,100),(100,115,85,105),(105,105,105,105)])
        r=run(df,stopLossPoints=10,takeProfitPoints=10)
        self.assertEqual(r['trades'][0]['exitPrice'],90)
        self.assertEqual(r['metrics']['netPnl'],-10)
        self.assertEqual(r['executionAudit']['ambiguousBars'],1)
        self.assertEqual(r['fills'][-1]['timePrecision'],'BAR_INTERVAL')
        self.assertEqual(r['fills'][-1]['barOpen'],'2024-01-01T01:00:00+00:00')
    def test_gap_stop_not_filled_at_unobserved_stop_and_cancels_add(self):
        df=frame([(100,100,100,100),(100,101,99,100),(70,71,69,70)])
        r=worker.backtest(df,plan(df,add=True),{**C,'stopLossPoints':10})
        self.assertEqual(r['trades'][0]['exitPrice'],70)
        self.assertEqual(r['metrics']['netPnl'],-30)
        self.assertEqual(len(r['trades']),1)
        self.assertEqual(r['rejections'][0]['reason'],'CANCELLED_AFTER_GAP_RISK_EXIT')
    def test_tp_limit_verify_tick_and_no_favorable_gap_windfall(self):
        df=frame([(100,100,100,100),(100,110,99,105),(120,121,119,120)])
        r=run(df,takeProfitPoints=10,tickSize=1,limitVerifyTicks=1)
        self.assertEqual(r['trades'][0]['exitPrice'],110)
        self.assertEqual(r['fills'][-1]['eventPhase'],'OPEN')
    def test_margin_low_triggers_even_when_close_recovers(self):
        df=frame([(100,100,100,100),(100,101,1,100),(100,100,100,100)])
        r=worker.backtest(df,plan(df,q=10),{**C,'capital':100.,'marginRate':.1,'stopOutPercent':50})
        # 100 + 10*(P-100) = 50% * 10*P*10%; P=900/9.5.
        self.assertEqual(r['trades'][0]['exitReason'],'MARGIN_STOP_OUT')
        self.assertAlmostEqual(r['trades'][0]['exitPrice'],900/9.5)
        self.assertEqual(r['metrics']['marginCalls'],1)
    def test_bid_ask_tick_fixed_minimum_fee_and_ledger(self):
        df=frame([(100,100,100,100),(100.03,102,99,100),(102.02,103,100,102.02)])
        r=run(df,spreadPoints=.2,tickSize=.1,commissionPerLot=.2,minimumCommission=1.)
        # buy ceil((100.03 + .20)/.1)*.1=100.3, exit floor(102.02/.1)*.1=102.
        self.assertAlmostEqual(r['metrics']['netPnl'],-.3)
        self.assertEqual(r['metrics']['totalCommission'],2)
        self.assertEqual(r['executionAudit']['status'],'PASS')
        for row in r['nav']:
            self.assertAlmostEqual(row['nav'],row['cash']+row['unrealizedPnl'])
            self.assertAlmostEqual(row['cash'],1000+row['realizedGrossPnl']-row['cumulativeCommission']-row['cumulativeFinancing'])
    def test_act365_financing_includes_gap_without_candles(self):
        df=frame([(100,100,100,100)]*3);df.index=pd.to_datetime(['2024-01-01T00:00Z','2024-01-01T01:00Z','2024-01-03T02:00Z'])
        r=run(df,annualFinancingPercent=36.5)
        # held 50 hours: 1h on Jan1 + 48h unobserved gap + 1h on Jan3.
        self.assertAlmostEqual(r['metrics']['totalFinancing'],100*.365*50/8760)
        self.assertAlmostEqual(r['metrics']['netPnl'],-r['metrics']['totalFinancing'])
        self.assertAlmostEqual(sum(d['financing'] for d in r['dailyNav']),r['metrics']['totalFinancing'])
        self.assertEqual(len(r['nav']),3)
    def test_risk_limits_and_equity_halt(self):
        df=frame([(100,100,100,100),(100,101,80,80),(80,100,80,100)])
        r=run(df,maxPositionQuantity=.5)
        self.assertEqual(r['rejections'][0]['reason'],'MAX_POSITION_QUANTITY')
        r=run(df,maxGrossLeverage=.05)
        self.assertEqual(r['rejections'][0]['reason'],'MAX_GROSS_LEVERAGE')
        r=run(df,equityStopPercent=1)
        self.assertTrue(r['metrics']['riskHalted'])
        self.assertEqual(r['trades'][0]['exitReason'],'EQUITY_DRAWDOWN_HALT')
    def test_insolvency_keeps_loss_and_halts_not_fabricated_zero_floor(self):
        df=frame([(100,100,100,100),(100,100,100,100),(1,1,1,1),(1,1,1,1)])
        r=worker.backtest(df,plan(df,q=10),{**C,'capital':100.,'marginRate':.01})
        self.assertEqual(r['metrics']['finalNav'],-890)
        self.assertTrue(r['metrics']['insolvent'])
        self.assertIsNone(r['metrics']['sharpe'])
        self.assertEqual(r['executionAudit']['status'],'PASS')
    def test_stale_gap_entry_rejected_and_prefix_invariant(self):
        df=frame([(100,100,100,100)]*5);df.index=pd.date_range('2024-01-01',periods=5,freq='2D',tz='UTC')
        r=run(df,maxEntryGapHours=12)
        self.assertEqual(r['rejections'][0]['reason'],'STALE_ENTRY_AFTER_GAP')
        c={**C,'stopLossPoints':10,'annualFinancingPercent':5}
        whole=worker.backtest(df,plan(df),c);prefix=worker.backtest(df.iloc[:3],plan(df.iloc[:3]),c)
        self.assertEqual(whole['nav'][:2],prefix['nav'][:2])
    def test_legacy_rejects_silently_ignored_settings(self):
        with self.assertRaisesRegex(ValueError,'OHLC'):execution.validate(dict(stopLossPoints=10))
        with self.assertRaises(ValueError):execution.validate(dict(executionMode='ohlc_conservative',stopOutPercent=0))
    def test_ask_margin_high_and_tp_crossing_order(self):
        df=frame([(100,100,100,100),(100,600,99,100),(100,100,100,100)])
        c={**C,'priceSide':'ASK','spreadBps':200,'marginRate':1}
        r=worker.backtest(df,plan(df,q=9),c)
        self.assertEqual(r['trades'][0]['exitReason'],'MARGIN_STOP_OUT')
        self.assertAlmostEqual(r['trades'][0]['exitPrice'],(100/.18)*.98)
        r=worker.backtest(df,plan(df,q=9),{**c,'takeProfitPoints':10})
        self.assertEqual(r['trades'][0]['exitReason'],'TAKE_PROFIT')
        self.assertAlmostEqual(r['metrics']['netPnl'],90)

if __name__=='__main__':unittest.main()
