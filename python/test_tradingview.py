import unittest
import pandas as pd
import numpy as np
import worker,tradingview
C=dict(capital=1000.,allocation=.95,commissionBps=0.,slippageBps=0.,spreadBps=0.,lotSize=.1,pointValue=1.,marginRate=1.,barHours=1,calendarVerified=False,executionMode='tv_reference')
def frame(rows):return pd.DataFrame([dict(open=o,high=h,low=l,close=c,volume=0.) for o,h,l,c in rows],index=pd.date_range('2024-01-01',periods=len(rows),freq='h',tz='UTC'))
def plan(df,orders):return dict(features=pd.DataFrame(dict(n=np.arange(len(df))),index=df.index),on_bar=lambda b,p,s,a:orders.get(b['n'],[]),params={},meta={'pyramiding':100})
def entry(q,id='buy'):return dict(type='entry',id=id,qty=q)
def close():return dict(type='close_all',id='exit')
class TradingViewTests(unittest.TestCase):
    def test_keep_open_separates_net_profit_and_equity_change(self):
        df=frame([(100,100,100,100),(100,125,95,120)])
        r=worker.backtest(df,plan(df,{0:[entry(2)]}),{**C,'commissionBps':100})
        self.assertEqual(len(r['trades']),0);self.assertEqual(len(r['openTrades']),1)
        self.assertEqual(r['metrics']['realizedNetPnl'],0)
        self.assertEqual(r['metrics']['openPnl'],38)
        self.assertEqual(r['metrics']['finalNav'],1038)
        self.assertAlmostEqual(r['reconciliationError'],0)
        forced=worker.backtest(df,plan(df,{0:[entry(2)]}),{**C,'commissionBps':100,'liquidateAtEnd':True})
        self.assertAlmostEqual(forced['metrics']['realizedNetPnl'],35.6)
        self.assertEqual(forced['metrics']['openPnl'],0)
    def test_pending_last_close_not_invented_fill(self):
        df=frame([(100,100,100,100),(100,110,100,110)])
        r=worker.backtest(df,plan(df,{0:[entry(1)],1:[close()]}),C)
        self.assertEqual(len(r['trades']),0);self.assertEqual(len(r['pendingAtEnd']),1)
    def test_drawdown_uses_closed_equity_peak_and_only_held_prices(self):
        df=frame([(100,100,100,100),(100,105,95,100),(110,120,1,110)])
        r=worker.backtest(df,plan(df,{0:[entry(2)],1:[close()]}),C)
        self.assertEqual(r['metrics']['tvMaxDrawdownAmount'],10)
        self.assertAlmostEqual(r['metrics']['tvMaxDrawdown'],.01)
        self.assertEqual(r['metrics']['realizedNetPnl'],20)
    def test_add_and_close_same_decision_closes_known_quantity_fifo(self):
        df=frame([(100,100,100,100)]*4)
        r=worker.backtest(df,plan(df,{0:[entry(2,'first')],1:[entry(1,'add'),close()]}),C)
        self.assertEqual(r['nav'][2]['quantity'],1)
        self.assertEqual(r['trades'][0]['orderId'],'first')
        self.assertEqual(r['openTrades'][0]['orderId'],'add')
    def test_documented_margin_cover_example(self):
        df=frame([(4.43,4.43,4.43,4.43),(4.43,4.43,3.9,3.9)])
        r=worker.backtest(df,plan(df,{0:[entry(682438)]}),{**C,'capital':1000000,'marginRate':.25,'lotSize':1})
        self.assertEqual(r['trades'][0]['quantity'],111052)
        self.assertEqual(r['trades'][0]['exitPrice'],3.9)
        self.assertEqual(r['nav'][-1]['quantity'],571386)
        self.assertAlmostEqual(r['reconciliationError'],0,places=6)
    def test_tick_slippage_and_process_close_are_explicit(self):
        df=frame([(100,101,99,100),(110,111,109,110)])
        p=plan(df,{0:[entry(1)]})
        normal=worker.backtest(df,p,{**C,'tickSize':.25,'slippageTicks':2})
        same=worker.backtest(df,p,{**C,'tickSize':.25,'slippageTicks':2,'processOnClose':True})
        self.assertEqual(normal['openTrades'][0]['price'],110.5)
        self.assertEqual(same['openTrades'][0]['price'],100.5)
        self.assertEqual(same['fills'][0]['eventPhase'],'CLOSE')
    def test_future_changes_cannot_change_completed_history(self):
        df=frame([(100,101,99,100)]*6);p=plan(df,{0:[entry(1)],2:[close()]})
        full=worker.backtest(df,p,C);prefix=worker.backtest(df.iloc[:4],plan(df.iloc[:4],{0:[entry(1)],2:[close()]}),C)
        self.assertEqual(full['nav'][:4],prefix['nav']);self.assertEqual(full['trades'],prefix['trades'])
    def test_reference_rejects_extra_hidden_costs_or_risk(self):
        with self.assertRaisesRegex(ValueError,'spreadPoints'):tradingview.validate({**C,'spreadPoints':1})
        with self.assertRaisesRegex(ValueError,'tick'):tradingview.validate({**C,'slippageTicks':2})
if __name__=='__main__':unittest.main()
