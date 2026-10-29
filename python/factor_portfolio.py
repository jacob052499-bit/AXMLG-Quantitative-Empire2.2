"""Causal fractional long/cash investing, FIFO realized PnL and post-cost weights."""
import numpy as np
import pandas as pd
import pyramiding

def capped_weights(raw,budget,cap):
    raw=np.asarray(raw,float);out=np.zeros(len(raw));active=raw>0;left=budget
    while active.any() and left>1e-12:
        proposal=left*raw[active]/raw[active].sum();indices=np.flatnonzero(active);over=proposal>cap+1e-12
        if not over.any():out[indices]=proposal;break
        capped=indices[over];out[capped]=cap;left-=cap*len(capped);active[capped]=False
    return out

def simulate(frames, scores, symbols, s, start, end, metric_fn, baseline=False, capital=None):
    capital=float(s['capital'] if capital is None else capital);cash=capital;count=len(frames);q=np.zeros(count);legs=[[] for _ in symbols]
    nav=[];fills=[];trades=[];rebalances=[];positions=[];peak=previous=capital;total_cost=turnover=0.;max_error=0.
    prices=np.column_stack([f.open.to_numpy() for f in frames]);closes=np.column_stack([f.close.to_numpy() for f in frames]);vol=np.column_stack([f.close.pct_change(fill_method=None).rolling(20).std(ddof=1).to_numpy() for f in frames])
    rate=(s['commissionBps']+s['slippageBps']+s['spreadBps']/2)/10000;commission_share=(s['commissionBps']/10000/rate) if rate else 0
    every=1 if count==1 else s['rebalanceBars'];top=count if baseline else min(count,s['topAssets']);cap=s['allocation'] if count==1 else s['maxAssetWeight'];on=False
    def trade_to(target,i,reference,time,decision,reason):
        nonlocal cash,total_cost,bar_cost
        target_q=target/reference;delta=target_q-q
        # Sells first, then buys. Every fill and partial FIFO close is inspectable.
        for j in sorted(range(count),key=lambda j:(delta[j]>0,j)):
            amount=float(delta[j]);size=abs(amount)
            if size<1e-12:continue
            notional=size*reference[j];cost=notional*rate;bar_cost+=cost;total_cost+=cost
            if amount>0:
                cash-=notional+cost;legs[j].append(dict(quantity=size,price=float(reference[j]),cost=cost,time=time,index=i))
            else:
                cash+=notional-cost;left=size;remaining=[]
                for l in legs[j]:
                    cut=min(left,l['quantity']);left-=cut
                    if cut>1e-12:
                        entry_cost=l['cost']*cut/l['quantity'];exit_cost=cut*reference[j]*rate;gross=cut*(reference[j]-l['price'])
                        trades.append(dict(id=len(trades)+1,symbol=symbols[j],entryTime=l['time'],exitTime=time,entryPrice=l['price'],exitPrice=float(reference[j]),quantity=cut,grossPnl=float(gross),commission=(entry_cost+exit_cost)*commission_share,executionDrag=(entry_cost+exit_cost)*(1-commission_share),netPnl=float(gross-entry_cost-exit_cost),barsHeld=i-l['index']+1,exitReason=reason))
                        if cut<l['quantity']-1e-12:remaining.append({**l,'quantity':l['quantity']-cut,'cost':l['cost']-entry_cost})
                    else:remaining.append(l)
                if left>1e-7:raise ValueError('FIFO 出場數量超過持倉。')
                legs[j]=remaining
            fills.append(dict(time=time,decisionTime=decision,featureAvailableTime=decision,symbol=symbols[j],side='BUY' if amount>0 else 'SELL',quantity=size,reference=float(reference[j]),price=float(reference[j]),notional=notional,cost=cost,commission=cost*commission_share,executionDrag=cost*(1-commission_share),reason=reason))
        q[:]=target_q
    for i in range(start,end):
        bar_cost=0.;current=q*prices[i];equity=float(cash+current.sum());held=bool(q.sum());decision=(frames[0].index[i-1]+pd.Timedelta(hours=s['barHours'])).isoformat()
        if (i-start)%every==0:
            lag=i-1;values=scores[lag];w=np.zeros(count);chosen=[]
            if baseline:chosen=list(range(count))
            elif count==1:
                if not np.isfinite(values[0]):on=False
                elif values[0]>=s['scoreThreshold']:on=True
                elif values[0]<s['exitThreshold']:on=False
                chosen=[0] if on else []
            else:
                ranked=sorted(np.flatnonzero(np.isfinite(values)),key=lambda j:(-values[j],symbols[j]));kept=[j for j in ranked[:top+s['rankBuffer']] if q[j]>1e-12]
                chosen=(kept+[j for j in ranked if j not in kept])[:top]
            if chosen:
                raw=np.zeros(count);raw[chosen]=1 if baseline or s['weighting']=='equal' else 1/np.maximum(vol[lag,chosen],1e-6)
                if not np.all(np.isfinite(raw)):raise ValueError('權重缺少前序波動資料。')
                w=capped_weights(raw,s['allocation'],cap)
            current_w=current/equity;delta=w-current_w;target_turn=float(np.abs(delta).sum());risk_override=bool((current_w>cap+1e-9).any() or current_w.sum()>s['allocation']+1e-9)
            if not risk_override and target_turn<s['rebalanceBand']:w=current_w.copy()
            elif not risk_override and target_turn>s['maxTargetTurnover']:w=current_w+delta*s['maxTargetTurnover']/target_turn
            if np.abs(w-current_w).sum()>1e-10:
                lo=0.;hi=equity
                for _ in range(45):
                    mid=(lo+hi)/2
                    if mid+rate*np.abs(mid*w-current).sum()>equity:hi=mid
                    else:lo=mid
                target=lo*w;notional=float(np.abs(target-current).sum());turnover+=notional/equity
                trade_to(target,i,prices[i],frames[0].index[i].isoformat(),decision,'REBALANCE')
                rebalances.append(dict(time=frames[0].index[i].isoformat(),decisionTime=decision,turnover=notional/equity,targetWeightTurnover=float(np.abs(w-current_w).sum()),riskOverride=risk_override,cost=bar_cost,weights={symbols[j]:float(w[j]) for j in range(count) if w[j]>1e-10},scores={symbols[j]:float(values[j]) for j in range(count) if np.isfinite(values[j])}))
        value=float(cash+(q*closes[i]).sum());held=held or bool(q.sum())
        if i==end-1 and q.sum()>1e-12:
            notional=float((q*closes[i]).sum());turnover+=notional/value
            trade_to(np.zeros(count),i,closes[i],(frames[0].index[i]+pd.Timedelta(hours=s['barHours'])).isoformat(),None,'END_OF_PERIOD');value=float(cash)
        if cash< -1e-6 or value<=0:raise ValueError('組合超支或資金耗盡。')
        peak=max(peak,value);nav.append(dict(date=frames[0].index[i].isoformat(),nav=value,cash=float(cash),quantity=float(q.sum()),**{'return':value/previous-1},pnl=value-previous,drawdown=1-value/peak,commission=bar_cost*commission_share,executionDrag=bar_cost*(1-commission_share),hadPosition=held));previous=value
        positions.append(dict(date=frames[0].index[i].isoformat(),weights={symbols[j]:float(q[j]*closes[i,j]/value) for j in range(count) if q[j]>1e-12},grossExposure=float((q*closes[i]).sum()/value)))
        max_error=max(max_error,abs(value-cash-float((q*closes[i]).sum())))
    config={**s,'capital':capital};daily=pyramiding.daily_nav(nav,capital,timezone=s['tradingTimezone']);m=metric_fn(daily,trades,config)
    m.update(maxDrawdown=max(x['drawdown'] for x in nav),totalCost=total_cost,turnover=turnover,rebalanceCount=len(rebalances),fillCount=len(fills),tradeCount=len(trades),netPnl=nav[-1]['nav']-capital)
    m.update(pyramiding.recovery_metrics(nav,capital,m['netPnl']));m['calmar']=m['cagr']/m['maxDrawdown'] if m['cagr'] is not None and m['maxDrawdown'] else None
    error=m['netPnl']-sum(t['netPnl'] for t in trades)
    return dict(nav=nav,dailyNav=daily,metrics=m,fills=fills,trades=trades,rebalances=rebalances,positions=positions,reconciliationError=error,executionAudit=dict(status='PASS' if max(max_error,abs(error))<max(1e-7,capital*1e-10) else 'FAIL',maxLedgerError=max_error,tradeNetError=abs(error)),scope='Fractional unlevered long/cash price returns; previous completed close -> next common observed open. Fixed bps costs on ALL traded notional, FIFO net PnL, explicit period-end liquidation. Target caps apply at rebalances; prices can drift afterward. Target-weight turnover cap can be overridden to correct concentration/exposure. No financing, corporate actions, FX, borrow, liquidity impact or Pepperstone CFD margin replication.')
