"""Pine v6 long market-order subset. Explicitly unverified against a user's TradingView export."""
import math
from collections import Counter
import pyramiding as core

def validate(c):
    for key,low,high in [('slippageTicks',0,10000),('pointValue',1e-8,1e6),('tickSize',0,1000),('marginRate',.00001,1)]:
        v=c.get(key,0 if key in ('slippageTicks','tickSize') else 1)
        if type(v) not in (int,float) or not math.isfinite(v) or not low<=v<=high:raise ValueError(key+' 超出範圍。')
    if int(c.get('slippageTicks',0))!=c.get('slippageTicks',0):raise ValueError('TradingView 滑價以整數 tick 設定。')
    if c.get('slippageTicks',0) and not c.get('tickSize'):raise ValueError('有 tick 滑價時必須填入 syminfo.mintick。')
    for k in ['spreadPoints','spreadBps','slippageBps','annualFinancingPercent','commissionPerLot','minimumCommission','stopLossPoints','takeProfitPoints','equityStopPercent','maxPositionQuantity','maxGrossLeverage','maxEntryGapHours']:
        if c.get(k,0):raise ValueError('TradingView 對照模式不套用 '+k+'；請使用 tick 滑價及 Pine 原始佣金，額外風控請切換研究模式。')
    for k in ['liquidateAtEnd','processOnClose']:
        if k in c and type(c[k]) is not bool:raise ValueError(k+' 必須為布林值。')

def backtest(frame,plan,c,metric_fn,start=0,end=None,delay=1,record=True):
    validate(c);end=len(frame) if end is None else end
    capital=float(c['capital']);balance=previous=peak=closed_peak=capital
    point=float(c.get('pointValue',1));margin=float(c.get('marginRate',1));lot=float(c['lotSize']);fee=c['commissionBps']/10000
    slip=c.get('slippageTicks',0)*c.get('tickSize',0);force=c.get('liquidateAtEnd',False)
    cache=core.prepared(frame,plan,c);legs=[];pending=[];state={};nav=[];trades=[];fills=[];decisions=[];rejects=[];cycles=[];cycle=None
    tv_dd=tv_dd_pct=0.;max_qty=max_legs=max_margin=max_notional=0.;margin_calls=0;ledger_error=0.;realized_gross=commissions=0.;insolvent=False
    cap=int(plan['meta'].get('pyramiding',100))
    if not 1<=cap<=100:raise ValueError('pyramiding 必須在 1–100。')
    def qty():return sum(l['quantity'] for l in legs)
    def equity(p):return balance+sum(l['quantity']*(p-l['price'])*point for l in legs)
    def observe(p):
        nonlocal tv_dd,tv_dd_pct,max_qty,max_legs,max_margin,max_notional,bar_dd,bar_dd_pct
        value=equity(p);tv_dd=max(tv_dd,closed_peak-value);tv_dd_pct=max(tv_dd_pct,(closed_peak-value)/closed_peak);bar_dd=max(bar_dd,closed_peak-value);bar_dd_pct=max(bar_dd_pct,(closed_peak-value)/closed_peak)
        max_qty=max(max_qty,qty());max_legs=max(max_legs,len(legs));max_notional=max(max_notional,qty()*p*point);max_margin=max(max_margin,qty()*p*point*margin)
    for i in range(start,end):
        row=cache['rows'][i];costs=drag=bar_dd=bar_dd_pct=0.;held=bool(legs)
        def execute(o,reference,time,phase='OPEN',close_quantity=None):
            nonlocal balance,costs,drag,held,cycle,closed_peak,realized_gross,commissions
            buy=o['type']=='entry';p=reference+(slip if buy else -slip)
            if p<=0:raise ValueError('tick 滑價造成非正成交價。')
            if buy:
                requested=float(o['qty']);q=math.floor((requested+lot*1e-9)/lot)*lot
                reason='ACCOUNT_INSOLVENT' if insolvent else 'OUTSIDE_ENTRY_SESSION' if not cache['entryOpen'][i] and phase=='OPEN' else 'INVALID_QUANTITY' if q<=0 else 'PYRAMIDING_LIMIT' if len(legs)>=cap else 'INSUFFICIENT_MARGIN' if equity(reference)-q*slip*point-q*p*point*fee<(qty()+q)*reference*point*margin-1e-9 else None
                if reason:rejects.append(dict(index=i,time=time,id=o['id'],requestedQuantity=requested,reason=reason));return
                commission=q*p*point*fee;balance-=commission;held=True
                if cycle is None:cycle=dict(id=len(cycles)+1,entryTime=time,entryIndex=i,entryQuantity=0.,entryNotional=0.,commission=0.,financing=0.,legCount=0,netPnl=0.)
                legs.append(dict(index=i,time=time,price=p,quantity=q,commission=commission,orderId=o['id'],cycleId=cycle['id']))
                cycle['entryQuantity']+=q;cycle['entryNotional']+=q*p;cycle['commission']+=commission;cycle['legCount']+=1
            else:
                q=min(qty(),close_quantity if close_quantity is not None else o.get('closeQty',qty()))
                if q<=1e-12:return
                commission=q*p*point*fee;left=q;remaining=[]
                for l in legs:
                    cut=min(left,l['quantity']);left-=cut
                    if cut>1e-12:
                        entry_fee=l['commission']*cut/l['quantity'];exit_fee=cut*p*point*fee;gross=cut*(p-l['price'])*point;net=gross-entry_fee-exit_fee
                        trades.append(dict(id=len(trades)+1,cycleId=l['cycleId'],orderId=l['orderId'],entryTime=l['time'],exitTime=time,entryPrice=l['price'],exitPrice=p,quantity=cut,grossPnl=gross,commission=entry_fee+exit_fee,financing=0.,netPnl=net,barsHeld=i-l['index']+1,exitReason=o['id'],timePrecision='BAR_INTERVAL' if phase=='INTRABAR' else 'BAR_BOUNDARY'))
                        balance+=gross-exit_fee;realized_gross+=gross;cycle['netPnl']+=net
                        if cut<l['quantity']-1e-12:remaining.append({**l,'quantity':l['quantity']-cut,'commission':l['commission']-entry_fee})
                    else:remaining.append(l)
                legs[:]=remaining;cycle['commission']+=commission
                if not legs:
                    cycle.update(exitTime=time,exitReason=o['id'],entryPrice=cycle['entryNotional']/cycle['entryQuantity'],exitPrice=p,quantity=cycle['entryQuantity'],barsHeld=i-cycle['entryIndex']+1)
                    cycles.append(cycle);cycle=None
                closed_peak=max(closed_peak,capital+sum(t['netPnl'] for t in trades))
            costs+=commission;commissions+=commission;drag+=q*abs(p-reference)*point
            if record:fills.append(dict(index=i,side='BUY' if buy else 'SELL',time=time,barOpen=cache['opens'][i],barClose=cache['closes'][i],timePrecision='BAR_INTERVAL' if phase=='INTRABAR' else 'BAR_BOUNDARY',eventPhase=phase,decisionTime=o.get('decisionTime'),featureAvailableTime=o.get('decisionTime'),sequence='PINE_MARKET_ORDER_SUBSET',reference=reference,price=p,quantity=q,commission=commission,executionDrag=q*abs(p-reference)*point,reason=o['id'],orderId=o['id']))
        due=[o for o in pending if o['due']==i];pending=[o for o in pending if o['due']!=i]
        for o in due:execute(o,row['open'],cache['opens'][i])
        # Four historical OHLC observations in the documented emulator path; no bar magnifier.
        path=[row['open'],row['high'],row['low'],row['close']] if abs(row['open']-row['high'])<abs(row['open']-row['low']) else [row['open'],row['low'],row['high'],row['close']]
        for j,p in enumerate(path):
            observe(p)
            if legs and equity(p)<qty()*p*point*margin-1e-9:
                deficit=qty()*p*point*margin-equity(p)
                cover=min(qty(),math.floor((deficit/(margin*p*point)+lot*1e-9)/lot)*lot*4)
                if cover>0:
                    margin_calls+=1;execute(dict(type='close_all',id='PINE_MARGIN_CALL'),p,cache['opens'][i] if j==0 else cache['closes'][i],'OPEN' if j==0 else 'INTRABAR',cover)
                    observe(p)
        if equity(row['close'])<=0:insolvent=True
        quantity=qty();pos=dict(quantity=quantity,openEntries=len(legs),averagePrice=sum(l['quantity']*l['price'] for l in legs)/quantity if quantity else None,equity=equity(row['close']))
        before=core.isolated_copy(state);orders=plan['on_bar'](cache['features'][i].copy(),pos,state,core.isolated_copy(plan['params']))
        if not isinstance(orders,list):raise ValueError('on_bar 必須回傳訂單 list。')
        for o in orders:
            if not isinstance(o,dict) or o.get('type') not in ('entry','close_all') or not isinstance(o.get('id'),str):raise ValueError('此對照子集僅支援 entry / close_all 市價單。')
            if o['type']=='entry' and (type(o.get('qty')) not in (int,float) or not math.isfinite(o['qty']) or o['qty']<=0):raise ValueError('qty 必須為正有限數。')
        if not cache['entryDecision'][i] and any(o['type']=='entry' for o in orders):orders=[o for o in orders if o['type']!='entry'];state.clear();state.update(before)
        for o in orders:
            order={**o,'decisionTime':cache['closes'][i],'due':i+delay}
            if o['type']=='close_all':order['closeQty']=quantity
            if c.get('processOnClose',False):execute(order,row['close'],cache['closes'][i],'CLOSE')
            else:pending=[x for x in pending if (x['id'],x['type'])!=(o['id'],o['type'])];pending.append(order)
        if record:decisions.append(dict(index=i,date=cache['opens'][i],decisionTime=cache['closes'][i],featureAvailableTime=cache['closes'][i],orders=core.isolated_copy(orders),state=core.isolated_copy(state),positionQuantity=quantity))
        if i==end-1 and force and legs:execute(dict(type='close_all',id='END_OF_PERIOD'),row['close'],cache['closes'][i],'FINAL_CLOSE')
        observe(row['close']);value=equity(row['close']);peak=max(peak,value)
        expected=capital+realized_gross-commissions;err=abs(balance-expected);ledger_error=max(ledger_error,err)
        nav.append(dict(date=cache['opens'][i],time=cache['times'][i],closeTime=cache['closes'][i],statisticsDate=cache['days'][i],nav=value,cash=balance,quantity=qty(),close=row['close'],**{'return':value/previous-1 if previous>0 else 0.},pnl=value-previous,drawdown=1-value/peak,commission=costs,executionDrag=drag,financing=0.,hadPosition=held,notional=qty()*row['close']*point,marginUsed=qty()*row['close']*point*margin,freeMargin=value-qty()*row['close']*point*margin,openEntries=len(legs),unrealizedPnl=value-balance,realizedGrossPnl=realized_gross,cumulativeCommission=commissions,cumulativeFinancing=0.,accountingError=err,intrabarDrawdown=bar_dd_pct,intrabarDrawdownAmount=bar_dd));previous=value
    daily=core.daily_nav(nav,capital,c.get('barHours',1),c.get('tradingTimezone','UTC'));m=metric_fn(daily,trades,c)
    open_gross=sum(l['quantity']*(nav[-1]['close']-l['price'])*point for l in legs);open_fee=sum(l['commission'] for l in legs);closed_net=sum(t['netPnl'] for t in trades)
    m.update(maxDailyDrawdown=m['maxDrawdown'],maxDrawdown=max(x['drawdown'] for x in nav),realizedNetPnl=closed_net,openPnl=open_gross-open_fee,openPnlGross=open_gross,openCommission=open_fee,openTradeCount=len(legs),closedTradeCount=len(trades),cycleCount=len(cycles),profitableCycles=sum(x['netPnl']>0 for x in cycles),maxPositionQuantity=max_qty,maxOpenEntries=max_legs,maxNotional=max_notional,maxMarginUsed=max_margin,marginCalls=margin_calls,forcedCloseCycles=sum(x['exitReason']=='END_OF_PERIOD' for x in cycles),barObservations=len(nav),totalFinancing=0.,tvMaxDrawdownAmount=tv_dd,tvMaxDrawdown=tv_dd_pct,reportNetPnl=closed_net,reportProfitToMdd=closed_net/tv_dd if tv_dd>1e-12 else None,reportMaxDrawdownAmount=tv_dd,reportMaxDrawdown=tv_dd_pct,metricFrequency='Calendar daily NAV statistics; TradingView-style drawdown uses closed-equity peak and OHLC extrema while held. Sharpe is not TradingView monthly Sharpe.',tradeUnit='closed entry leg / FIFO partial close; open entries separate',insolvent=insolvent)
    m.update(core.recovery_metrics(nav,capital,m['netPnl']));m['calmar']=m['cagr']/m['maxDrawdown'] if m['cagr'] is not None and m['maxDrawdown'] else None
    if insolvent:m.update(sharpe=None,sortino=None,cagr=None,calmar=None)
    if not c.get('calendarVerified',False):
        for month in m['monthly']:month['complete']=False
        m.update(completeMonths=0,positiveMonths=0,positiveMonthRatio=None)
    error=m['netPnl']-closed_net-(open_gross-open_fee)
    return dict(nav=nav,dailyNav=daily,trades=trades,openTrades=[dict(**l,unrealizedPnl=l['quantity']*(nav[-1]['close']-l['price'])*point-l['commission']) for l in legs],cycles=cycles,openCycle=cycle,fills=fills,decisions=decisions,rejected=len(rejects),rejections=rejects,pendingAtEnd=pending,metrics=m,reconciliationError=error,
      executionAudit=dict(status='PASS' if max(ledger_error,abs(error))<max(1e-7,capital*1e-10) else 'FAIL',barsChecked=len(nav),maxLedgerError=ledger_error,finalTradeError=abs(error),rejectionReasons=dict(Counter(x['reason'] for x in rejects)),exitReasons=dict(Counter(t['exitReason'] for t in cycles)),insolvent=insolvent),
      executionModel=dict(engine='pyramiding',version='PINE_V6_MARKET_SUBSET_V1',pointValue=point,marginRate=margin,pyramiding=cap,specificationStatus='REFERENCE_EXPORT_NOT_YET_RECONCILED',priceSide='CHART_OHLC_NO_IMPLICIT_SPREAD',settings=dict(executionMode='tv_reference',tickSize=c.get('tickSize',0),slippageTicks=c.get('slippageTicks',0),commissionBps=c['commissionBps'],marginRate=margin,liquidateAtEnd=force,processOnClose=c.get('processOnClose',False)),path='Open-high-low-close if open closer to high, otherwise open-low-high-close; OHLC-only historical observations. No bar magnifier.',limits='Long market orders / FIFO close subset, explicit qty and point value. No Pine compiler, stop/limit orders, security(), FX, tick data or externally verified parity. Different price feeds/history produce different signals. Same-bar add+close sizes closing order from the position known at decision.',financing='No extra financing, spread or risk-manager modifications in reference mode.',endOfPeriod='Keep open trades and pending orders unless explicit liquidation selected.',statistics='Closed net PnL, open PnL and total equity change separate. Daily Sharpe is not TradingView monthly Sharpe.'))
