"""Next-observed-open long CFD proxy. No broker parity or intrabar liquidation claim."""
import copy
import math
import numpy as np
import pandas as pd
import time_rules


def recovery_metrics(nav,capital,net_pnl):
    peak=capital;amount=0.
    for row in nav:peak=max(peak,row['nav']);amount=max(amount,peak-row['nav'])
    return dict(maxDrawdownAmount=amount,profitToMdd=net_pnl/amount if amount>1e-12 else None,profitToMddStatus='DEFINED' if amount>1e-12 else 'NO_DRAWDOWN',profitToMddDefinition='Net PnL after costs / maximum peak-to-trough equity drawdown in currency; initial capital included. Bar-close observations; not intrabar MDD.')


def isolated_copy(value):
    if isinstance(value,dict) and all(type(v) in (str,int,float,bool,type(None)) for v in value.values()):return value.copy()
    return copy.deepcopy(value)


def prepared(frame,plan,c):
    cache=plan.get('_cache')
    key=(id(frame),c.get('barHours',1),c.get('tradingTimezone','UTC'),c.get('sessionEnabled',False),c.get('sessionStart'),c.get('sessionEnd'),tuple(c.get('sessionDays',[])))
    if cache and cache['key']==key:return cache
    delta=pd.Timedelta(hours=c.get('barHours',1));dates=frame.index
    cache=dict(key=key,frame=frame,rows=frame.to_dict('records'),features=plan['features'].to_dict('records'),opens=[d.isoformat() for d in dates.to_pydatetime()],closes=[d.isoformat() for d in (dates+delta).to_pydatetime()],times=dates.as_unit('ms').asi8.tolist(),days=dates.tz_convert(c.get('tradingTimezone','UTC')).strftime('%Y-%m-%d').tolist(),entryOpen=time_rules.allowed(dates,c),entryDecision=time_rules.allowed(dates+delta,c))
    plan['_cache']=cache
    return cache


def execution_price(reference, side, c):
    full = c['spreadBps']/10000*reference+c.get('spreadPoints',0)
    slip = c['slippageBps']/10000*reference
    quote = c.get('priceSide','UNKNOWN')
    spread = (full if side=='BUY' else 0) if quote=='BID' else (0 if side=='BUY' else -full) if quote=='ASK' else full/2*(1 if side=='BUY' else -1)
    return reference+slip*(1 if side=='BUY' else -1)+spread


def rma(x, n):
    n = int(n)
    if n < 1: raise ValueError('RMA period must be positive')
    values = x.to_numpy(dtype=float); out = np.full(len(x), np.nan)
    seed = []; prev = None
    for i, value in enumerate(values):
        if not np.isfinite(value):
            if prev is not None: out[i] = prev
            continue
        if prev is None:
            seed.append(value)
            if len(seed) == n: prev = float(np.mean(seed)); out[i] = prev
        else: prev = (prev*(n-1)+value)/n; out[i] = prev
    return pd.Series(out, index=x.index)


def pine_rsi(x, n=14):
    d = x.diff(); up = rma(d.clip(lower=0), n); down = rma(-d.clip(upper=0), n)
    value = 100-100/(1+up/down)
    return value.where(down != 0, 100).where(up != 0, 0).where(up.notna() & down.notna())


def pine_atr(df, n=14):
    prev = df.close.shift(1)
    tr = pd.concat([df.high-df.low, (df.high-prev).abs(), (df.low-prev).abs()], axis=1).max(axis=1)
    return rma(tr, n)


def daily_nav(nav, capital, bar_hours=1, timezone='UTC'):
    """UTC calendar-day mark-to-market. Non-observed dates carry NAV, without making candles."""
    grouped = {}
    for row in nav:
        if row.get('statisticsDate'):day=row['statisticsDate']
        elif timezone=='UTC':day=row['date'][:10]
        else:
            stamp=pd.Timestamp(row['date']);stamp=stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp
            day = str(stamp.tz_convert(timezone).date())
        if day not in grouped: grouped[day] = {**row, 'commission': 0., 'executionDrag': 0., 'hadPosition': False, **({'financing':0.} if 'financing' in row else {})}
        g = grouped[day]; fee = g['commission']+row['commission']; drag = g['executionDrag']+row['executionDrag']; held = g['hadPosition'] or row['hadPosition']
        financing=g.get('financing',0)+row.get('financing',0)
        g.update(row); g.update(commission=fee, executionDrag=drag, hadPosition=held)
        if 'financing' in row:g['financing']=financing
    result = []; previous = peak = capital
    for day in pd.date_range(min(grouped), max(grouped), freq='D', tz=timezone):
        key = str(day.date()); row = grouped.get(key)
        if row is None:
            row = {**result[-1], 'commission': 0., 'executionDrag': 0., 'hadPosition': result[-1]['quantity'] > 0, 'observed': False, **({'financing':0.} if 'financing' in result[-1] else {})}
        else: row = {**row, 'observed': True}
        peak = max(peak, row['nav']); row.update(date=key, time=int(day.timestamp()*1000), pnl=row['nav']-previous, **{'return': row['nav']/previous-1 if previous>0 else 0.}, drawdown=1-row['nav']/peak)
        result.append(row); previous = row['nav']
    return result


def backtest(frame, plan, c, metric_fn, start=0, end=None, delay=1, record=True):
    if c.get('executionMode')=='tv_reference':
        import tradingview
        return tradingview.backtest(frame,plan,c,metric_fn,start,end,delay,record)
    if c.get('executionMode')=='ohlc_conservative':
        import execution
        return execution.backtest(frame,plan,c,metric_fn,start,end,delay,record)
    end = end or len(frame); balance = previous = peak = float(c['capital'])
    legs = []; pending = []; nav = []; fills = []; trades = []; cycles = []; decisions = []; rejects = []; state = {}; cycle = None
    point = float(c.get('pointValue', 1)); margin = float(c.get('marginRate', 1)); lot = float(c['lotSize'])
    if not math.isfinite(point) or point <= 0 or not math.isfinite(margin) or not 0 < margin <= 1: raise ValueError('pointValue 必須正數，marginRate 必須在 (0,1]。')
    adverse = (c['slippageBps']+c['spreadBps']/2)/10000; fee = c['commissionBps']/10000
    cache=prepared(frame,plan,c);rows=cache['rows'];features=cache['features'];opens=cache['opens'];closes=cache['closes']
    cap = int(plan['meta'].get('pyramiding', 100))
    if not 1 <= cap <= 100: raise ValueError('pyramiding 必須在 1–100。')
    max_qty = max_notional = max_margin = max_legs = 0; margin_calls = 0
    hours = c.get('barHours', 1)
    entry_open=cache['entryOpen'];entry_decision=cache['entryDecision']
    def qty(): return sum(l['quantity'] for l in legs)
    def equity(price): return balance+sum(l['quantity']*(price-l['price'])*point for l in legs)
    for i in range(start, end):
        row = rows[i]; costs = drag = 0.; held = bool(legs)
        def execute(order, reference, timestamp, forced=False):
            nonlocal balance, costs, drag, cycle, held
            side = 'BUY' if order['type'] == 'entry' else 'SELL'
            price = execution_price(reference,side,c)
            if price<=0: raise ValueError('成交代理價小於零，請檢查價差。')
            if side == 'BUY':
                requested = float(order['qty']); quantity = math.floor((requested+lot*1e-9)/lot)*lot
                if not entry_open[i]: reason = 'OUTSIDE_ENTRY_SESSION'
                elif not math.isfinite(quantity) or quantity <= 0: reason = 'INVALID_QUANTITY'
                elif len(legs) >= cap: reason = 'PYRAMIDING_LIMIT'
                elif equity(reference)-quantity*(price-reference)*point-quantity*price*point*fee < (qty()+quantity)*price*point*margin: reason = 'INSUFFICIENT_MARGIN'
                else: reason = None
                if reason:
                    rejects.append({'index': i, 'time': timestamp, 'id': order['id'], 'requestedQuantity': requested, 'reason': reason}); return
                cost = quantity*price*point*fee; balance -= cost; held = True
                if cycle is None: cycle = {'id': len(cycles)+1, 'entryTime': timestamp, 'entryIndex': i, 'entryQuantity': 0., 'entryNotional': 0., 'commission': 0., 'legCount': 0, 'netPnl': 0.}
                leg = dict(index=i, time=timestamp, price=price, quantity=quantity, commission=cost, orderId=order['id'], cycleId=cycle['id'])
                legs.append(leg); cycle['entryQuantity'] += quantity; cycle['entryNotional'] += quantity*price; cycle['commission'] += cost; cycle['legCount'] += 1
            else:
                quantity = qty()
                if not legs: return
                cost = quantity*price*point*fee
                for leg in legs:
                    exit_fee = leg['quantity']*price*point*fee; gross = leg['quantity']*(price-leg['price'])*point; net = gross-leg['commission']-exit_fee
                    trades.append(dict(id=len(trades)+1, cycleId=cycle['id'], orderId=leg['orderId'], entryTime=leg['time'], exitTime=timestamp, entryPrice=leg['price'], exitPrice=price, quantity=leg['quantity'], grossPnl=gross, commission=leg['commission']+exit_fee, netPnl=net, barsHeld=i-leg['index']+1, exitReason=order['id']))
                    balance += gross-exit_fee; cycle['netPnl'] += net; cycle['commission'] += exit_fee
                cycle.update(exitTime=timestamp, exitReason=order['id'], entryPrice=cycle['entryNotional']/cycle['entryQuantity'], exitPrice=price, quantity=cycle['entryQuantity'], barsHeld=i-cycle['entryIndex']+1)
                cycles.append(cycle); cycle = None; legs.clear()
            execution_drag = quantity*abs(price-reference)*point; costs += cost; drag += execution_drag
            if record: fills.append(dict(index=i, side=side, time=timestamp, decisionTime=order.get('decisionTime', timestamp), featureAvailableTime=order.get('decisionTime'), sequence='FINAL_CLOSE_LIQUIDATION' if forced else 'COMPLETED_CLOSE_THEN_NEXT_OBSERVED_OPEN', reference=reference, price=price, quantity=quantity, commission=cost, executionDrag=execution_drag, reason=order['id'], orderId=order['id']))
        due = [o for o in pending if o['due'] == i]; pending = [o for o in pending if o['due'] != i]
        for order in due: execute(order, row['open'], opens[i])
        max_qty=max(max_qty,qty());max_legs=max(max_legs,len(legs))
        observed_notional=qty()*max(row['open'],row['close'])*point
        max_notional=max(max_notional,observed_notional);max_margin=max(max_margin,observed_notional*margin)
        # Close-based margin model: conservative full liquidation; never claimed to match Pine's partial intrabar calls.
        if legs and equity(row['close']) < qty()*row['close']*point*margin:
            margin_calls += 1; execute(dict(type='close_all', id='MARGIN_LIQUIDATION_PROXY'), row['close'], closes[i], True)
        quantity = qty(); notional = quantity*row['close']*point
        max_qty = max(max_qty, quantity); max_notional = max(max_notional, notional); max_margin = max(max_margin, notional*margin); max_legs = max(max_legs, len(legs))
        pos = dict(quantity=quantity, openEntries=len(legs), averagePrice=sum(l['quantity']*l['price'] for l in legs)/quantity if quantity else None, equity=equity(row['close']))
        before=isolated_copy(state) if not entry_decision[i] else None
        orders = plan['on_bar'](features[i].copy(), pos, state, isolated_copy(plan['params']))
        if not isinstance(orders, list): raise ValueError('on_bar 須回傳訂單 list。')
        if not entry_decision[i] and any(o.get('type')=='entry' for o in orders):
            orders=[o for o in orders if o.get('type')!='entry'];state.clear();state.update(before)
        # An add and close_all at the same close has broker-emulator ordering ambiguity; fail explicitly.
        if any(o.get('type') == 'entry' for o in orders) and any(o.get('type') == 'close_all' for o in orders): raise ValueError('同根同時加倉與 close_all；需先確認 TradingView 訂單先後，未假造等價結果。')
        for o in orders:
            if o.get('type') not in ['entry', 'close_all'] or not isinstance(o.get('id'), str): raise ValueError('訂單須有 type=entry/close_all 與字串 id。')
            if o['type'] == 'entry' and (not isinstance(o.get('qty'), (int,float)) or not math.isfinite(o['qty']) or o['qty'] <= 0): raise ValueError('entry qty 必須是有限正數。')
            pending.append({**o, 'due': i+delay, 'decisionTime': closes[i]})
        if record: decisions.append(dict(index=i, date=opens[i], decisionTime=closes[i], featureAvailableTime=closes[i], orders=[isolated_copy(o) for o in orders], state=isolated_copy(state), positionQuantity=quantity))
        if i == end-1 and legs: execute(dict(type='close_all', id='END_OF_PERIOD'), row['close'], closes[i], True)
        value = equity(row['close']); peak = max(peak, value)
        if not math.isfinite(value) or value <= 0: raise ValueError('帳戶權益耗盡；停止回測，未繼續計算負 NAV 報酬。')
        closing_notional=qty()*row['close']*point
        nav.append(dict(date=opens[i], time=cache['times'][i], closeTime=closes[i], statisticsDate=cache['days'][i],nav=value, cash=balance, quantity=qty(), close=row['close'], **{'return': value/previous-1}, pnl=value-previous, drawdown=1-value/peak, commission=costs, executionDrag=drag, hadPosition=held, notional=closing_notional, marginUsed=closing_notional*margin, openEntries=len(legs))); previous=value
    daily = daily_nav(nav, c['capital'], hours, c.get('tradingTimezone','UTC')); m = metric_fn(daily, trades, c)
    m.update(maxDrawdown=max(n['drawdown'] for n in nav), maxDailyDrawdown=m['maxDrawdown'], cycleCount=len(cycles), profitableCycles=sum(x['netPnl']>0 for x in cycles), maxPositionQuantity=max_qty, maxOpenEntries=max_legs, maxNotional=max_notional, maxMarginUsed=max_margin, marginCalls=margin_calls, barObservations=len(nav), metricFrequency=c.get('tradingTimezone','UTC')+' calendar daily NAV (bar open date); MDD=observed bar closes', tradeUnit='entry leg; cycle=all entries until flat', cashMeaning='CFD balance, excludes reserved margin; NAV=balance+unrealized PnL', forcedCloseCycles=sum(x['exitReason']=='END_OF_PERIOD' for x in cycles))
    m['calmar'] = m['cagr']/m['maxDrawdown'] if m['cagr'] is not None and m['maxDrawdown'] else None
    m.update(recovery_metrics(nav,c['capital'],m['netPnl']))
    # A session-unverified input cannot certify complete trading months.
    if not c.get('calendarVerified', False):
        for month in m['monthly']: month['complete'] = False
        m.update(completeMonths=0, positiveMonths=0, positiveMonthRatio=None)
    return dict(nav=nav, dailyNav=daily, trades=trades, cycles=cycles, fills=fills, decisions=decisions, metrics=m, rejected=len(rejects), rejections=rejects[:2000], pendingAtEnd=pending, reconciliationError=m['netPnl']-sum(t['netPnl'] for t in trades), executionModel=dict(engine='pyramiding', pointValue=point, marginRate=margin, spreadPoints=c.get('spreadPoints',0), specificationStatus=c.get('specificationStatus','USER_ASSUMPTION_NOT_BROKER_VERIFIED'), pyramiding=cap, sizing='explicit qty; no automatic cash allocation', margin='close-based full-liquidation proxy; not TradingView partial intrabar margin parity', financing='0; swaps / funding / dividends not modeled', calendarVerified=c.get('calendarVerified', False)))
