"""Deterministic long-CFD OHLC research model; conservative path, not broker ticks."""
import math
from collections import Counter
import pyramiding as core

DEFAULTS = dict(executionMode='legacy', tickSize=0., commissionPerLot=0., minimumCommission=0.,
                annualFinancingPercent=0., stopLossPoints=0., takeProfitPoints=0.,
                stopOutPercent=100., maxPositionQuantity=0., maxGrossLeverage=0.,
                equityStopPercent=0., maxEntryGapHours=0., limitVerifyTicks=1)


def validate(c):
    mode=c.get('executionMode','legacy')
    if mode=='tv_reference':
        import tradingview
        return tradingview.validate(c)
    if mode not in ('legacy','ohlc_conservative'):raise ValueError('未知 executionMode。')
    for k,upper in dict(tickSize=1000,commissionPerLot=10000,minimumCommission=10000,
                        annualFinancingPercent=100,stopLossPoints=100000,takeProfitPoints=100000,
                        stopOutPercent=100,maxPositionQuantity=1e7,maxGrossLeverage=1000,
                        equityStopPercent=99,maxEntryGapHours=1000,limitVerifyTicks=100).items():
        v=c.get(k,DEFAULTS[k])
        if type(v) not in (int,float) or not math.isfinite(v) or v<0 or v>upper:raise ValueError(k+' 超出合法範圍。')
    if c.get('stopOutPercent',100)<=0:raise ValueError('stopOutPercent 必須大於零。')
    if int(c.get('limitVerifyTicks',1))!=c.get('limitVerifyTicks',1):raise ValueError('limitVerifyTicks 必須為整數。')
    if mode=='legacy' and any(c.get(k,DEFAULTS[k])!=DEFAULTS[k] for k in DEFAULTS if k!='executionMode'):
        raise ValueError('進階成交／風控設定須搭配 OHLC 保守模式，避免設定被忽略。')


def backtest(frame,plan,config,metric_fn,start=0,end=None,delay=1,record=True):
    c={**DEFAULTS,**config};validate(c)
    end=len(frame) if end is None else end
    capital=float(c['capital']);balance=previous=peak=capital
    point=float(c.get('pointValue',1));margin=float(c.get('marginRate',1));lot=float(c['lotSize']);tick=c['tickSize']
    if point<=0 or not math.isfinite(point) or not 0<margin<=1:raise ValueError('pointValue / marginRate 無效。')
    cap=int(plan['meta'].get('pyramiding',100))
    if not 1<=cap<=100:raise ValueError('pyramiding 必須在 1–100。')
    cache=core.prepared(frame,plan,c);rows=cache['rows'];opens=cache['opens'];closes=cache['closes']
    legs=[];pending=[];nav=[];fills=[];trades=[];cycles=[];decisions=[];rejects=[];events=[];state={};cycle=None
    realized=fees_total=financing_total=0.;max_error=0.;halted=False;insolvent=False
    max_qty=max_notional=max_margin=max_legs=0.;ambiguous=gap_exits=margin_calls=0
    def qty():return sum(l['quantity'] for l in legs)
    def average():return sum(l['quantity']*l['price'] for l in legs)/qty() if legs else None
    def snap(v,side):
        return (math.ceil(v/tick-1e-9) if side=='BUY' else math.floor(v/tick+1e-9))*tick if tick else v
    def price(ref,side):return snap(core.execution_price(ref,side,c),side)
    # Mark with the executable bid quote, without hypothetical future exit commission/slippage.
    quote=c.get('priceSide','UNKNOWN');fraction=0 if quote=='BID' else 1 if quote=='ASK' else .5
    bid_a=1-fraction*c['spreadBps']/10000;bid_b=-fraction*c.get('spreadPoints',0)
    def bid(ref):return ref*bid_a+bid_b
    def equity(ref):return balance+sum(l['quantity']*(bid(ref)-l['price'])*point for l in legs)
    def commission(q,p):return max(c['minimumCommission'],q*p*point*c['commissionBps']/10000+q*c['commissionPerLot'])
    def reject(i,o,reason):rejects.append(dict(index=i,time=opens[i],id=o['id'],requestedQuantity=o.get('qty'),reason=reason))
    for i in range(start,end):
        row=rows[i];costs=drag=financing=0.;held=bool(legs);risk_exit=False
        gap_hours=max(0,(cache['times'][i]-cache['times'][i-1])/3600000-c.get('barHours',1)) if i>start else 0.
        def accrue(hours,reference):
            nonlocal balance,financing,financing_total
            if not legs or not c['annualFinancingPercent'] or hours<=0:return
            charge=qty()*reference*point*c['annualFinancingPercent']/100*hours/(365*24)
            for l in legs:l['financing']+=charge*l['quantity']/qty()
            balance-=charge;financing+=charge;financing_total+=charge;cycle['financing']+=charge
        def execute(o,reference,timestamp,phase='OPEN',limit_price=None):
            nonlocal balance,costs,drag,cycle,held,realized,fees_total,risk_exit
            side='BUY' if o['type']=='entry' else 'SELL';p=price(reference,side) if limit_price is None else limit_price
            if p<=0:raise ValueError('成交價小於零，請檢查價差／tick。')
            if side=='BUY':
                requested=float(o['qty']);q=math.floor((requested+lot*1e-9)/lot)*lot
                fee=commission(q,p) if q>0 else 0.;post=equity(reference)-q*(p-bid(reference))*point-fee
                reason='RISK_HALTED' if halted else 'RISK_EXIT_SAME_BAR' if risk_exit else 'OUTSIDE_ENTRY_SESSION' if not cache['entryOpen'][i] else 'STALE_ENTRY_AFTER_GAP' if c['maxEntryGapHours'] and gap_hours>c['maxEntryGapHours'] else 'INVALID_QUANTITY' if q<=0 else 'PYRAMIDING_LIMIT' if len(legs)>=cap else 'MAX_POSITION_QUANTITY' if c['maxPositionQuantity'] and qty()+q>c['maxPositionQuantity']+1e-10 else 'MAX_GROSS_LEVERAGE' if c['maxGrossLeverage'] and (qty()+q)*reference*point>max(post,0)*c['maxGrossLeverage'] else 'INSUFFICIENT_MARGIN' if post<(qty()+q)*reference*point*margin else None
                if reason:reject(i,o,reason);return
                balance-=fee;held=True
                if cycle is None:cycle=dict(id=len(cycles)+1,entryTime=timestamp,entryIndex=i,entryQuantity=0.,entryNotional=0.,commission=0.,financing=0.,legCount=0,netPnl=0.)
                legs.append(dict(index=i,time=timestamp,price=p,quantity=q,commission=fee,financing=0.,orderId=o['id'],cycleId=cycle['id']))
                cycle['entryQuantity']+=q;cycle['entryNotional']+=q*p;cycle['commission']+=fee;cycle['legCount']+=1
            else:
                if not legs:return
                q=qty();fee=commission(q,p)
                for l in legs:
                    exit_fee=fee*l['quantity']/q;gross=l['quantity']*(p-l['price'])*point;net=gross-l['commission']-exit_fee-l['financing']
                    trades.append(dict(id=len(trades)+1,cycleId=cycle['id'],orderId=l['orderId'],entryTime=l['time'],exitTime=timestamp,entryPrice=l['price'],exitPrice=p,quantity=l['quantity'],grossPnl=gross,commission=l['commission']+exit_fee,financing=l['financing'],netPnl=net,barsHeld=i-l['index']+1,exitReason=o['id'],timePrecision='BAR_INTERVAL' if phase=='INTRABAR' else 'BAR_BOUNDARY'))
                    balance+=gross-exit_fee;realized+=gross;cycle['netPnl']+=net
                cycle['commission']+=fee;cycle.update(exitTime=timestamp,exitReason=o['id'],entryPrice=cycle['entryNotional']/cycle['entryQuantity'],exitPrice=p,quantity=q,barsHeld=i-cycle['entryIndex']+1)
                cycles.append(cycle);cycle=None;legs.clear()
                if o.get('risk'):
                    risk_exit=True;state.clear()
                    if record:events.append(dict(index=i,time=timestamp,barOpen=opens[i],barClose=closes[i],type=o['id'],phase=phase,reference=reference,price=p,quantity=q))
            costs+=fee;fees_total+=fee;execution_drag=q*abs(p-reference)*point;drag+=execution_drag
            if record:fills.append(dict(index=i,side=side,time=timestamp,barOpen=opens[i],barClose=closes[i],timePrecision='BAR_INTERVAL' if phase=='INTRABAR' else 'BAR_BOUNDARY',eventPhase=phase,decisionTime=o.get('decisionTime'),featureAvailableTime=o.get('decisionTime'),sequence='RISK_OHLC_CONSERVATIVE' if o.get('risk') else 'FINAL_CLOSE_LIQUIDATION' if phase=='FINAL_CLOSE' else 'COMPLETED_CLOSE_THEN_NEXT_OBSERVED_OPEN',reference=reference,price=p,quantity=q,commission=fee,executionDrag=execution_drag,reason=o['id'],orderId=o['id']))
        def protection(phase):
            nonlocal ambiguous,gap_exits,margin_calls
            if not legs:return
            av=average();q=qty();lo=row['open'] if phase=='OPEN' else row['low'];hi=row['open'] if phase=='OPEN' else row['high']
            # Stop prices are bid triggers; pending protection follows weighted filled entry price.
            stop=(av-c['stopLossPoints']-bid_b)/bid_a if c['stopLossPoints'] else None
            target=snap(av+c['takeProfitPoints'],'BUY') if c['takeProfitPoints'] else None
            target_ref=(target+tick*c['limitVerifyTicks']-bid_b)/bid_a if target is not None else None
            stop_hit=stop is not None and lo<=stop
            margin_slope=bid_a-margin*c['stopOutPercent']/100
            margin_check=lo if margin_slope>=0 else hi
            margin_hit=equity(margin_check)<=q*margin_check*point*margin*c['stopOutPercent']/100
            margin_threshold=(sum(l['quantity']*l['price']*point for l in legs)-balance-q*point*bid_b)/(q*point*margin_slope) if abs(margin_slope)>1e-12 else row['open']
            target_hit=target_ref is not None and hi>=target_ref
            # With ASK/MID references and 100% margin, growing spread can make the
            # maintenance requirement bind on an upward move. A lower TP is crossed first.
            if phase=='INTRABAR' and margin_slope<0 and target_hit and target_ref<margin_threshold:margin_hit=False
            if target_hit and (stop_hit or margin_hit):ambiguous+=1
            if stop_hit or margin_hit:
                threshold=stop if stop_hit else -math.inf;reason='STOP_LOSS'
                if margin_hit and (not stop_hit or margin_slope>=0 and margin_threshold>=threshold):threshold=margin_threshold;reason='MARGIN_STOP_OUT'
                upward_margin=reason=='MARGIN_STOP_OUT' and margin_slope<0
                ref=row['open'] if phase=='OPEN' else min(row['high'],max(row['open'],threshold)) if upward_margin else max(row['low'],min(row['open'],threshold))
                if reason=='MARGIN_STOP_OUT':margin_calls+=1
                if phase=='OPEN':gap_exits+=1
                execute(dict(type='close_all',id=reason,risk=True),ref,opens[i] if phase=='OPEN' else closes[i],phase)
            elif target_hit:
                if phase=='OPEN':gap_exits+=1
                execute(dict(type='close_all',id='TAKE_PROFIT',risk=True),(target-bid_b)/bid_a,opens[i] if phase=='OPEN' else closes[i],phase,limit_price=target)
        # Carry positions across missing observations; never invent prices in the gap.
        accrue(gap_hours,rows[i-1]['close'] if i>start else row['open'])
        protection('OPEN')
        due=[o for o in pending if o['due']==i];pending=[o for o in pending if o['due']!=i]
        for o in due:
            if risk_exit:
                if o['type']=='entry':reject(i,o,'CANCELLED_AFTER_GAP_RISK_EXIT')
            else:execute(o,row['open'],opens[i])
        max_qty=max(max_qty,qty());max_legs=max(max_legs,len(legs));notional=qty()*row['high']*point
        max_notional=max(max_notional,notional);max_margin=max(max_margin,notional*margin)
        # ACT/365 notional financing proxy. An intrabar exit pays one full observed bar (conservative).
        accrue(c.get('barHours',1),row['open'])
        protection('INTRABAR')
        if risk_exit:
            for o in pending:
                if o['type']=='entry':reject(i,o,'CANCELLED_AFTER_RISK_EXIT')
            pending.clear()
        current=equity(row['close']);peak=max(peak,current)
        if c['equityStopPercent'] and current<=peak*(1-c['equityStopPercent']/100):
            halted=True
            if legs:execute(dict(type='close_all',id='EQUITY_DRAWDOWN_HALT',risk=True),row['close'],closes[i],'CLOSE')
        if equity(row['close'])<=0:
            insolvent=halted=True
            if legs:execute(dict(type='close_all',id='ACCOUNT_INSOLVENT',risk=True),row['close'],closes[i],'CLOSE')
        quantity=qty();pos=dict(quantity=quantity,openEntries=len(legs),averagePrice=average(),equity=equity(row['close']),riskHalted=halted)
        before=core.isolated_copy(state)
        orders=plan['on_bar'](cache['features'][i].copy(),pos,state,core.isolated_copy(plan['params']))
        if not isinstance(orders,list):raise ValueError('on_bar 須回傳訂單 list。')
        for o in orders:
            if not isinstance(o,dict) or o.get('type') not in ('entry','close_all') or not isinstance(o.get('id'),str):raise ValueError('訂單須有 type=entry/close_all 與字串 id。')
            if o['type']=='entry' and (type(o.get('qty')) not in (int,float) or not math.isfinite(o['qty']) or o['qty']<=0):raise ValueError('entry qty 必須是有限正數。')
        if halted or risk_exit or not cache['entryDecision'][i]:
            if any(o['type']=='entry' for o in orders):
                orders=[o for o in orders if o['type']!='entry'];state.clear();state.update(before)
        if any(o['type']=='entry' for o in orders) and any(o['type']=='close_all' for o in orders):raise ValueError('同根同時 entry 與 close_all，請指定單一操作。')
        for o in orders:pending.append({**o,'due':i+delay,'decisionTime':closes[i]})
        if record:decisions.append(dict(index=i,date=opens[i],decisionTime=closes[i],featureAvailableTime=closes[i],orders=core.isolated_copy(orders),state=core.isolated_copy(state),positionQuantity=quantity))
        if i==end-1 and legs:execute(dict(type='close_all',id='END_OF_PERIOD'),row['close'],closes[i],'FINAL_CLOSE')
        value=equity(row['close']);unrealized=value-balance;expected=capital+realized-fees_total-financing_total
        independent_unrealized=sum(l['quantity']*(bid(row['close'])-l['price'])*point for l in legs)
        error=max(abs(balance-expected),abs(value-(expected+independent_unrealized)));max_error=max(max_error,error)
        if not math.isfinite(value) or not math.isfinite(error):raise ValueError('帳戶含非有限數值。')
        peak=max(peak,value);notional=qty()*row['close']*point
        nav.append(dict(date=opens[i],time=cache['times'][i],closeTime=closes[i],statisticsDate=cache['days'][i],nav=value,cash=balance,quantity=qty(),close=row['close'],**{'return':value/previous-1 if previous>0 else 0.},pnl=value-previous,drawdown=1-value/peak,commission=costs,executionDrag=drag,financing=financing,hadPosition=held,notional=notional,marginUsed=notional*margin,freeMargin=value-notional*margin,marginLevel=value/(notional*margin)*100 if notional else None,openEntries=len(legs),realizedGrossPnl=realized,cumulativeCommission=fees_total,cumulativeFinancing=financing_total,unrealizedPnl=unrealized,accountingError=error,riskHalted=halted));previous=value
    daily=core.daily_nav(nav,capital,c.get('barHours',1),c.get('tradingTimezone','UTC'));m=metric_fn(daily,trades,c)
    m.update(maxDailyDrawdown=m['maxDrawdown'],maxDrawdown=max(n['drawdown'] for n in nav),cycleCount=len(cycles),profitableCycles=sum(x['netPnl']>0 for x in cycles),maxPositionQuantity=max_qty,maxOpenEntries=int(max_legs),maxNotional=max_notional,maxMarginUsed=max_margin,marginCalls=margin_calls,barObservations=len(nav),metricFrequency=c.get('tradingTimezone','UTC')+' calendar daily NAV (bar open date); MDD=observed bar closes',tradeUnit='entry leg; cycle=all entries until flat',cashMeaning='CFD balance; equity uses executable bid before exit fees/slippage',forcedCloseCycles=sum(x['exitReason']=='END_OF_PERIOD' for x in cycles),totalFinancing=financing_total,ambiguousBars=ambiguous,gapRiskExits=gap_exits,riskHalted=halted,insolvent=insolvent)
    m['calmar']=m['cagr']/m['maxDrawdown'] if m['cagr'] is not None and m['maxDrawdown'] else None
    m.update(core.recovery_metrics(nav,capital,m['netPnl']))
    if insolvent:m.update(sharpe=None,sortino=None,cagr=None,calmar=None)
    if not c.get('calendarVerified',False):
        for month in m['monthly']:month['complete']=False
        m.update(completeMonths=0,positiveMonths=0,positiveMonthRatio=None)
    reconciliation=m['netPnl']-sum(t['netPnl'] for t in trades)
    return dict(nav=nav,dailyNav=daily,trades=trades,cycles=cycles,fills=fills,decisions=decisions,metrics=m,rejected=len(rejects),rejections=rejects,pendingAtEnd=pending,reconciliationError=reconciliation,
                executionAudit=dict(status='PASS' if max(max_error,abs(reconciliation))<=max(1e-7,capital*1e-10) else 'FAIL',barsChecked=len(nav),maxLedgerError=max_error,finalTradeError=abs(reconciliation),rejectionReasons=dict(Counter(r['reason'] for r in rejects)),exitReasons=dict(Counter(t['exitReason'] for t in cycles)),ambiguousBars=ambiguous,gapRiskExits=gap_exits,events=events[-2000:],insolvent=insolvent),
                executionModel=dict(engine='pyramiding',version='OHLC_CONSERVATIVE_V1',pointValue=point,marginRate=margin,pyramiding=cap,spreadPoints=c.get('spreadPoints',0),priceSide=quote,specificationStatus=c.get('specificationStatus','USER_ASSUMPTION_NOT_BROKER_VERIFIED'),settings={k:c[k] for k in DEFAULTS},
                  path='Existing gap protection before pending market orders; then orders at open; then adverse stop/margin before target if both touched. No same-bar re-entry after protective exit; strategy state reset.',
                  brackets='Whole-position weighted filled entry +/- fixed points; recomputed on additions. Bid triggers. TP limit at target, no favorable gap improvement; verify N ticks beyond. Stop/market adverse spread/slippage/tick rounding.',
                  margin='Equity / current reference-notional margin; configurable maintenance %. Entire position liquidated, not broker partial stop-out.',
                  exposure='Max notional / margin are conservative bar-high exposure bounds; actual intrabar exposure path unknown.',
                  financing='Optional positive annual long notional debit ACT/365. Includes unobserved gaps. Intrabar exit charged full bar; no broker swap calendars, triple swaps, dividends or FX.',
                  timestamps='Intrabar event time is bar close placeholder; actual event is somewhere in [barOpen,barClose]. No tick precision claim.',
                  limitations='OHLC cannot establish intrabar path/liquidity, true drawdown or broker quotes. Gaps may cause losses beyond stops; negative balance retained and trading halted.',calendarVerified=c.get('calendarVerified',False)))
