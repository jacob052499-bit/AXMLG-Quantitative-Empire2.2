"""Local trusted-code research worker. This is a process boundary, not a security sandbox."""
import contextlib
import copy
import datetime as dt
import hashlib
import inspect
import itertools
import json
import math
import os
import sys
import traceback
import platform
from pathlib import Path

import numpy as np
import pandas as pd
import pyramiding
import time_rules
import bar_periods
import execution
import time
from functools import lru_cache

VERSION = "0.10.0"


class Indicators:
    """Causal trailing indicators; no backfill or centered windows."""
    @staticmethod
    def sma(x, n=20): return x.rolling(int(n), min_periods=int(n)).mean()
    rma = staticmethod(pyramiding.rma)
    pine_rsi = staticmethod(pyramiding.pine_rsi)
    pine_atr = staticmethod(pyramiding.pine_atr)
    @staticmethod
    def ema(x, n=20): return x.ewm(span=int(n), adjust=False, min_periods=int(n)).mean()
    @staticmethod
    def rsi(x, n=14):
        change = x.diff(); up = change.clip(lower=0).rolling(int(n)).mean(); down = (-change.clip(upper=0)).rolling(int(n)).mean()
        return (100 * up / (up + down)).where((up + down) != 0, 50)
    @staticmethod
    def atr(data, n=14):
        prev = data.close.shift(1)
        tr = pd.concat([data.high-data.low, (data.high-prev).abs(), (data.low-prev).abs()], axis=1).max(axis=1)
        return tr.rolling(int(n)).mean()
    @staticmethod
    def bollinger(x, n=20, k=2):
        mid = Indicators.sma(x, n); sd = x.rolling(int(n)).std(ddof=1)
        return pd.DataFrame({"lower": mid-k*sd, "middle": mid, "upper": mid+k*sd})
    @staticmethod
    def macd(x, fast=12, slow=26, signal=9):
        line = Indicators.ema(x, fast)-Indicators.ema(x, slow); sig=Indicators.ema(line, signal)
        return pd.DataFrame({"macd":line, "signal":sig, "histogram":line-sig})
    @staticmethod
    def zscore(x, n=20): return (x-Indicators.sma(x,n))/x.rolling(int(n)).std(ddof=1)
    @staticmethod
    def roc(x, n=10): return x.pct_change(int(n), fill_method=None)
    @staticmethod
    def volatility(x, n=20, annualization=365): return x.pct_change(fill_method=None).rolling(int(n)).std(ddof=1)*math.sqrt(annualization)
    @staticmethod
    def donchian(data, n=20): return pd.DataFrame({"upper":data.high.shift(1).rolling(int(n)).max(), "lower":data.low.shift(1).rolling(int(n)).min()})


def clean(value):
    if isinstance(value, dict): return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [clean(v) for v in value]
    if isinstance(value, np.ndarray): return clean(value.tolist())
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (float,np.floating)): return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_,)): return bool(value)
    if isinstance(value, (pd.Timestamp,dt.datetime,dt.date)): return value.isoformat()
    if value is None or isinstance(value,(str,int,bool)): return value
    return str(value)[:4000]


@lru_cache(maxsize=16)
def compiled_source(code):return compile(code, 'strategy.py', 'exec')

def namespace(code, frame, params):
    scope={"__name__":"__strategy__", "df":frame.copy(), "params":copy.deepcopy(params), "np":np, "pd":pd, "ta":Indicators}
    exec(compiled_source(code), scope)
    return scope


def fit_model(code, train, params):
    scope=namespace(code,train,params)
    if "fit" in scope:
        if not callable(scope["fit"]): raise ValueError("fit 必須是函式。")
        return scope["fit"](train.copy(),copy.deepcopy(params))
    return None


def signals_for(code, frame, params, model=None):
    scope=namespace(code,frame,params)
    if callable(scope.get('features')) and callable(scope.get('on_bar')):
        features=scope['features'](frame.copy(),copy.deepcopy(params))
        if not isinstance(features,pd.DataFrame) or not features.index.equals(frame.index) or not len(features.columns): raise ValueError('features 須回傳同 index 的非空 DataFrame。')
        if not all(pd.api.types.is_numeric_dtype(features[k]) for k in features): raise ValueError('features 只接受數值欄位。')
        if np.isinf(features.to_numpy(dtype=float)).any(): raise ValueError('features 包含無限值。')
        return {'features':features,'on_bar':scope['on_bar'],'params':copy.deepcopy(params),'meta':scope.get('STRATEGY_META',{})}
    if callable(scope.get("strategy")):
        fn=scope["strategy"]
        value=fn(frame.copy(),copy.deepcopy(params),copy.deepcopy(model)) if len(inspect.signature(fn).parameters)>=3 else fn(frame.copy(),copy.deepcopy(params))
    elif "signals" in scope: value=scope["signals"]
    else: raise ValueError("回測策略須定義 strategy(df, params[, model])，或建立 signals 序列。純程式請選『僅執行 Python』。")
    if isinstance(value,pd.DataFrame):
        if "position" not in value: raise ValueError("DataFrame 結果需有 position 欄位。")
        value=value["position"]
    if isinstance(value,pd.Series) and not value.index.equals(frame.index): raise ValueError("策略 Series 的 index 必須與 df.index 完全一致，禁止重排或省略日期。")
    values=np.asarray(value,dtype=float)
    if values.ndim!=1 or len(values)!=len(frame): raise ValueError("策略必須回傳與 df 等長的一維 0/1 持倉訊號。")
    # Warmup NaN means no signal; infinite values are always errors.
    if np.isinf(values).any(): raise ValueError("訊號包含無限值。")
    values=np.nan_to_num(values,nan=0)
    if not np.isin(values,[0,1]).all(): raise ValueError("本版策略訊號只能是 0（空倉）或 1（做多），不接受槓桿或做空。")
    return values


def quantile(values,p): return float(np.quantile(values,p)) if len(values) else None
def finite_ratio(a,b): return float(a/b) if a is not None and b is not None and abs(b)>1e-15 else None


def metrics(nav,trades,c):
    n=len(nav); returns=np.array([r["return"] for r in nav]); pnls=np.array([t["netPnl"] for t in trades])
    initial=c["capital"]; final=nav[-1]["nav"]; total=final/initial-1; years=n/365.25
    avg=float(returns.mean()); sd=float(returns.std(ddof=1)) if n>1 else 0
    exponent=math.log(final/initial)/years if years and final>0 else None
    cagr=math.expm1(exponent) if exponent is not None and exponent<700 else None
    drawdown=max(x["drawdown"] for x in nav); wins=pnls[pnls>1e-8]; losses=pnls[pnls< -1e-8]
    monthly=[]; yearly=[]
    series=pd.Series(returns,index=pd.to_datetime([r["date"] for r in nav],utc=True))
    pnl_series=pd.Series([r['pnl'] for r in nav],index=series.index)
    for key,group in series.groupby(series.index.strftime('%Y-%m')):
        monthly.append({"month":key,"return":float(np.prod(1+group)-1),"pnl":float(pnl_series.loc[group.index].sum()),"bars":len(group),"complete":len(group)==group.index[0].days_in_month})
    for key,group in series.groupby(series.index.year): yearly.append({"year":int(key),"return":float(np.prod(1+group)-1),"bars":len(group)})
    complete=[x for x in monthly if x["complete"]];positive_months=sum(x["return"]>0 for x in complete)
    downside=math.sqrt(float(np.mean(np.minimum(returns,0)**2)))
    var=float(np.quantile(returns,.05)); tail=returns[returns<=var]
    duration=longest=0
    for row in nav:
        duration=duration+1 if row["drawdown"]>1e-12 else 0;longest=max(longest,duration)
    longest_win=longest_loss=win_streak=loss_streak=0
    for p in pnls:
        win_streak=win_streak+1 if p>1e-8 else 0;loss_streak=loss_streak+1 if p< -1e-8 else 0
        longest_win=max(longest_win,win_streak);longest_loss=max(longest_loss,loss_streak)
    return clean({**pyramiding.recovery_metrics(nav,initial,final-initial),"initialNav":initial,"finalNav":final,"netPnl":final-initial,"totalReturn":total,"cagr":cagr,"sharpe":finite_ratio(avg,sd)*math.sqrt(365) if sd>1e-15 else None,"calmar":finite_ratio(cagr,drawdown),"sortino":finite_ratio(avg,downside)*math.sqrt(365) if downside>1e-15 else None,"maxDrawdown":drawdown,"tradeCount":len(trades),"wins":len(wins),"losses":len(losses),"flat":len(pnls)-len(wins)-len(losses),"winRate":len(wins)/len(pnls) if len(pnls) else None,"profitFactor":float(wins.sum()/-losses.sum()) if len(losses) else 'Infinity' if len(wins) else None,"payoffRatio":float(wins.mean()/-losses.mean()) if len(wins) and len(losses) else None,"avgTradePnl":float(pnls.mean()) if len(pnls) else None,"medianTradePnl":float(np.median(pnls)) if len(pnls) else None,"totalCommission":sum(x["commission"] for x in nav),"totalExecutionDrag":sum(x["executionDrag"] for x in nav),"exposure":sum(x["hadPosition"] for x in nav)/n,"completeMonths":len(complete),"positiveMonths":positive_months,"positiveMonthRatio":positive_months/len(complete) if complete else None,"worstDay":float(returns.min()),"bestDay":float(returns.max()),"dailyMean":avg,"dailyStd":sd,"dailyMedian":float(np.median(returns)),"dailyPnlMean":float(np.mean([x["pnl"] for x in nav])),"dailyPnlMedian":float(np.median([x["pnl"] for x in nav])),"positiveDays":int(sum(returns>1e-12)),"negativeDays":int(sum(returns< -1e-12)),"zeroDays":int(sum(np.abs(returns)<=1e-12)),"annualization":365,"riskFreeRate":0,"sampleStdDDoF":1,"observations":n,"tradeR":None,"tradeRReason":"未定義入場停損風險分母","monthly":monthly,"yearly":yearly,"var95":max(0,-var),"expectedShortfall95":max(0,-float(tail.mean())) if len(tail) else None,"maxDrawdownDuration":longest,"unrecoveredDrawdownDays":duration,"longestWinStreak":longest_win,"longestLossStreak":longest_loss,"averageHoldingBars":float(np.mean([t["barsHeld"] for t in trades])) if trades else None})


def backtest(frame,signals,c,start=0,end=None,delay=1,record=True):
    if isinstance(signals,dict): return pyramiding.backtest(frame,signals,c,metrics,start,end,delay,record)
    end=end or len(frame); cash=previous=peak=c["capital"]; quantity=0.;entry=None;nav=[];trades=[];fills=[];decisions=[];rejected=0
    adverse=(c["slippageBps"]+c["spreadBps"]/2)/10000;fee=c["commissionBps"]/10000
    dates=frame.index; data=frame.to_dict('records')
    entry_open=time_rules.allowed(dates,c);entry_decision=time_rules.allowed(dates+pd.Timedelta(hours=c.get('barHours',24)),c)
    for i in range(start,end):
        row=data[i]; commission=drag=0.; held=quantity>0;signal_index=i-delay;target=bool(signals[signal_index]) if signal_index>=0 else False
        decision_time=(dates[signal_index]+pd.Timedelta(hours=c.get('barHours',24))).isoformat() if signal_index>=0 else dates[i].isoformat()
        def execute(side,reference,time,reason):
            nonlocal cash,quantity,entry,commission,drag,held,rejected
            price=pyramiding.execution_price(reference,side,c) if c.get('barHours',24)<24 else reference*(1+adverse if side=='BUY' else 1-adverse)
            qty=math.floor(cash*c["allocation"]/(price*(1+fee))/c["lotSize"])*c["lotSize"] if side=='BUY' else quantity
            if qty<=0:rejected+=1;return
            cost=qty*price*fee; execution_drag=qty*abs(price-reference);commission+=cost;drag+=execution_drag
            if record:fills.append({"index":i,"side":side,"time":time,"decisionTime":time if reason=='END_OF_PERIOD' else decision_time,"featureAvailableTime":None if reason=='END_OF_PERIOD' else decision_time,"sequence":'FINAL_CLOSE_LIQUIDATION' if reason=='END_OF_PERIOD' else 'PREVIOUS_CLOSE_THEN_NEXT_OBSERVED_OPEN',"reference":reference,"price":price,"quantity":qty,"commission":cost,"executionDrag":execution_drag,"reason":reason})
            if side=='BUY':cash-=qty*price+cost;quantity=qty;entry={"index":i,"time":time,"price":price,"commission":cost};held=True
            else:
                cash+=qty*price-cost;gross=qty*(price-entry["price"])
                trades.append({"id":len(trades)+1,"entryTime":entry["time"],"exitTime":time,"entryPrice":entry["price"],"exitPrice":price,"quantity":qty,"grossPnl":gross,"commission":entry["commission"]+cost,"netPnl":gross-entry["commission"]-cost,"barsHeld":i-entry["index"]+1,"exitReason":reason})
                quantity=0.;entry=None
        if target and quantity==0 and entry_open[i] and signal_index>=0 and entry_decision[signal_index]:execute('BUY',row["open"],dates[i].isoformat(),'SIGNAL')
        elif not target and quantity>0:execute('SELL',row["open"],dates[i].isoformat(),'SIGNAL')
        if record:decisions.append({"index":i,"date":str(dates[i].date()),"decisionTime":decision_time,"featureAvailableTime":decision_time if signal_index>=0 else None,"signalBar":str(dates[signal_index].date()) if signal_index>=0 else None,"target":'LONG' if target else 'CASH'})
        if i==end-1 and quantity>0:execute('SELL',row["close"],(dates[i]+pd.Timedelta(hours=c.get('barHours',24))).isoformat(),'END_OF_PERIOD')
        equity=cash+quantity*row["close"];peak=max(peak,equity)
        if not math.isfinite(equity) or equity<=0 or cash< -1e-6:raise ValueError('帳戶資金耗盡、超支或 NAV 無效。')
        nav.append({"date":dates[i].isoformat() if c.get('barHours',24)!=24 else str(dates[i].date()),"time":int(dates[i].timestamp()*1000),"nav":equity,"cash":cash,"quantity":quantity,"close":row["close"],"return":equity/previous-1,"pnl":equity-previous,"drawdown":1-equity/peak,"commission":commission,"executionDrag":drag,"hadPosition":held});previous=equity
    daily=pyramiding.daily_nav(nav,c['capital'],timezone=c.get('tradingTimezone','UTC')) if c.get('barHours',24)!=24 or c.get('tradingTimezone','UTC')!='UTC' else nav
    m=metrics(daily,trades,c);error=m["netPnl"]-sum(t["netPnl"] for t in trades)
    if c.get('barHours',24)<24:
        m['maxDailyDrawdown']=m['maxDrawdown'];m['maxDrawdown']=max(x['drawdown'] for x in nav)
        m['calmar']=finite_ratio(m['cagr'],m['maxDrawdown'])
    m.update(pyramiding.recovery_metrics(nav,c["capital"],m["netPnl"]))
    if not c.get('calendarVerified',False):
        for month in m['monthly']:month['complete']=False
        m.update(completeMonths=0,positiveMonths=0,positiveMonthRatio=None)
    return {"nav":nav,"dailyNav":daily,"trades":trades,"fills":fills,"decisions":decisions,"metrics":m,"rejected":rejected,"reconciliationError":error}


def candidate_list(params,grid):
    if not grid:return [copy.deepcopy(params)]
    names=list(grid)
    if len(names)>4:raise ValueError('參數搜尋最多四個軸；3D 圖以固定切片顯示。')
    if any(not isinstance(grid[k],list) or not 1<=len(grid[k])<=1000 for k in names):raise ValueError('每個參數軸需為 1–1000 個數值。')
    if math.prod(len(grid[k]) for k in names)>100000:raise ValueError('候選全集最多 100,000 組。')
    for name in names:
        if not all(isinstance(x,(float,int)) and not isinstance(x,bool) and math.isfinite(x) for x in grid[name]):raise ValueError('參數軸只接受有限數字。')
        if len(set(grid[name]))!=len(grid[name]):raise ValueError('參數軸不可有重複數值。')
    return [{**params,**dict(zip(names,values))} for values in itertools.product(*(grid[k] for k in names))]


def plateau(trials,grid):
    names=list(grid)
    if not names:return {"axes":{},"cells":[],"definition":"未啟用參數網格。"}
    axes={k:sorted(grid[k]) for k in names}
    indices={k:{v:i for i,v in enumerate(axes[k])} for k in names}
    measured={tuple(indices[k][t['params'][k]] for k in names):t for t in trials}
    for coordinate,t in measured.items():
        neighbors=[];total=0
        ranges=[range(max(0,v-1),min(len(axes[k]),v+2)) for k,v in zip(names,coordinate)]
        for adjacent in itertools.product(*ranges):
            other=measured.get(adjacent)
            if other is None:continue
            total+=1
            if other['eligible']:neighbors.append(other['metrics']['sharpe'])
        t['neighborhood']={'n':len(neighbors),'total':total,'eligibleFraction':len(neighbors)/total if total else 0,'medianSharpe':float(np.median(neighbors)) if neighbors else None,'stdSharpe':float(np.std(neighbors,ddof=1)) if len(neighbors)>1 else None,'positiveFraction':sum(x>0 for x in neighbors)/len(neighbors) if neighbors else None}
    cells=[{"trialId":t["trialId"],"params":t["params"],"sharpe":t["metrics"]["sharpe"] if t["metrics"] else None,"eligible":t["eligible"],"neighborhood":t["neighborhood"],"metrics":t['metrics'],"reasons":t.get('reasons',[]),"error":t.get('error')} for t in trials]
    return {"axes":axes,"cells":cells,"definition":"IS 網格相鄰 ±1 格（含自身）：依合格鄰點中位 Sharpe 選參，須至少 3 個合格鄰點且占實測鄰域至少 60%。同時保留鄰域標準差與樣本數；不是統計顯著性。"}


def select_trials(code,frame,c,params,grid,start,end,selector,search_options=None):
    from parameter_search import run as search
    options=search_options or {}
    trials,summary=search(code,frame,c,params,grid,start,end,workers=options.get('workers',2),checkpoint=options.get('checkpoint',(Path.cwd()/'input.json').exists()))
    pp=plateau(trials,grid);pp["search"]={k:summary[k] for k in ["total","completed","failed","eligible","marketBars","scope"]}
    eligible=[t for t in trials if t["eligible"]]
    if selector=='plateau':eligible=[t for t in eligible if t.get('neighborhood',{}).get('n',0)>=3 and t['neighborhood']['eligibleFraction']>=.6]
    def score(t):return t['neighborhood']['medianSharpe'] if selector=='plateau' else t['metrics']['sharpe']
    eligible.sort(key=lambda t:(-score(t),json.dumps(t['params'],sort_keys=True)))
    selected=eligible[0] if eligible else None
    # A fixed-parameter run does not require selection eligibility, but still reports samples honestly.
    if not grid and trials[0]['status']=='COMPLETED':selected=trials[0]
    if not selected:
        return params,None,trials,pp
    return selected['params'],selected['trialId'],trials,pp


def causal_audit(code,frame,params,model,start,full,c=None):
    checkpoints=sorted(set(int(x) for x in np.linspace(start+2,len(frame)-2,3)))
    checks=[]
    for cut in checkpoints:
        if cut<=0 or cut>=len(frame):continue
        try:
            prefix=signals_for(code,frame.iloc[:cut],params,model)
            if isinstance(full,dict):
                a=prefix['features'].to_numpy();b=full['features'].iloc[:cut].to_numpy()
                feature_diff=np.flatnonzero(~np.all((a==b)|(np.isnan(a)&np.isnan(b)),axis=1))
                f=backtest(frame,full,c,start,cut)['decisions'];p=backtest(frame.iloc[:cut],prefix,c,start,cut)['decisions']
                diff=list(feature_diff)+[j+start for j,(x,y) in enumerate(zip(f,p)) if x!=y]
            else: diff=np.flatnonzero(prefix!=full[:cut])
            checks.append({"prefixBars":cut,"mismatches":int(len(diff)),"firstMismatch":int(diff[0]) if len(diff) else None})
        except Exception as e:checks.append({"prefixBars":cut,"error":str(e),"mismatches":None})
    perturbed=frame.copy(); boundary=max(start+1,len(frame)//2)
    perturbed.iloc[boundary:,perturbed.columns.get_indexer(['open','high','low','close'])]*=1.731
    try:
        changed=signals_for(code,perturbed,params,model)
        if isinstance(full,dict):
            a=changed['features'].iloc[:boundary].to_numpy();b=full['features'].iloc[:boundary].to_numpy();diff=list(np.flatnonzero(~np.all((a==b)|(np.isnan(a)&np.isnan(b)),axis=1)))
            f=backtest(frame,full,c,start,boundary)['decisions'];p=backtest(perturbed,changed,c,start,boundary)['decisions']
            diff += [j+start for j,(x,y) in enumerate(zip(f,p)) if x!=y]
        else: diff=np.flatnonzero(changed[:boundary]!=full[:boundary])
        checks.append({"futurePerturbationFrom":boundary,"mismatches":int(len(diff)),"firstMismatch":int(diff[0]) if len(diff) else None})
    except Exception as e:checks.append({"futurePerturbationFrom":boundary,"error":str(e),"mismatches":None})
    return {"status":"PASS_SAMPLED" if checks and all(x.get('mismatches')==0 for x in checks) else 'FAILED',"checks":checks,"limitation":"抽樣 prefix 與未來擾動只檢查訊號對未來行情的依賴，不證明任意 Python 程式、外部檔案或自訂模型完全因果。"}


def monte_carlo(nav,capital,settings):
    from risk_simulation import simulate
    return clean(simulate(nav,capital,settings))


def distribution_tools(nav):
    s=pd.Series([x['return'] for x in nav]);hist,edges=np.histogram(s,bins=min(20,max(5,int(math.sqrt(len(s))))))
    rolling=[]
    for window in [30,90]:
        avg=s.rolling(window).mean();sd=s.rolling(window).std(ddof=1);ratio=avg/sd*math.sqrt(365)
        rolling.append({"window":window,"values":[{"date":row['date'],"sharpe":float(ratio.iloc[i]) if pd.notna(ratio.iloc[i]) and math.isfinite(ratio.iloc[i]) else None,"volatility":float(sd.iloc[i]*math.sqrt(365)) if pd.notna(sd.iloc[i]) else None} for i,row in enumerate(nav)]})
    return clean({"histogram":[{"low":float(edges[i]),"high":float(edges[i+1]),"count":int(count)} for i,count in enumerate(hist)],"quantiles":{str(p):quantile(s,p) for p in [.01,.05,.25,.5,.75,.95,.99]},"autocorrelation":[{"lag":lag,"value":float(s.autocorr(lag)) if s.std()>1e-15 and len(s)>lag+1 else None} for lag in [1,5,10,20]],"rolling":rolling})


def walk_forward(code,frame,c,params,grid,settings,selector):
    train=int(settings.get('trainBars',365));test=int(settings.get('testBars',126));gap=int(settings.get('gapBars',5));mode=settings.get('window','expanding');limit=int(settings.get('maxFolds',5))
    if train<60 or test<20 or gap<0 or gap>250 or mode not in ['expanding','rolling'] or not 1<=limit<=12:raise ValueError('Walk-forward 設定無效。')
    folds=[];stitched=[];capital=c['capital'];start=train+gap
    while start<len(frame) and len(folds)<limit:
        end=min(start+test,len(frame));train_end=start-gap;train_start=0 if mode=='expanding' else train_end-train
        p,winner,trials,pp=select_trials(code,frame,c,params,grid,train_start,train_end,selector)
        if winner is None and grid:signals=np.zeros(end)
        else:model=fit_model(code,frame.iloc[train_start:train_end],p);signals=signals_for(code,frame.iloc[:end],p,model)
        result=backtest(frame.iloc[:end],signals,{**c,'capital':capital,'liquidateAtEnd':True},start,end,record=False)
        folds.append({"fold":len(folds)+1,"trainStart":str(frame.index[train_start].date()),"trainEnd":str(frame.index[train_end-1].date()),"testStart":str(frame.index[start].date()),"testEnd":str(frame.index[end-1].date()),"gapBars":gap,"params":p,"winner":winner,"candidateCount":len(trials),"trials":trials,"metrics":result['metrics'],"reconciliationError":result['reconciliationError'],"evidence":"KNOWN_EVALUATION"})
        stitched.extend(result['nav']);capital=result['metrics']['finalNav'];start=end
        if capital<=0:break
    if not folds:return {"status":"INSUFFICIENT_SAMPLE","folds":[],"reason":"資料不足以容納訓練與 gap。"}
    peak=c['capital']
    for row in stitched:peak=max(peak,row['nav']);row['drawdown']=1-row['nav']/peak
    return {"status":"COMPLETED","folds":folds,"nav":stitched,"totalReturn":capital/c['capital']-1,"maxDrawdown":max(x['drawdown'] for x in stitched),"positiveFoldFraction":sum(x['metrics']['netPnl']>0 for x in folds)/len(folds),"remainingBars":len(frame)-start,"scope":"每 fold 只用前序訓練選參；fold 結束按契約平倉並扣成本，下一 fold 以延續現金、空倉重新開始。折間結果有時間依賴，已查看歷史不是 untouched forward。"}


def code_outputs(scope):
    outputs=scope.get('outputs',{});tables=[];plots=[]
    if not isinstance(outputs,dict):outputs={"result":outputs}
    for name,value in outputs.items():
        if isinstance(value,pd.Series):value=value.to_frame(name=str(value.name or 'value'))
        if isinstance(value,pd.DataFrame):tables.append({"name":str(name),"rows":clean(value.head(300).reset_index().to_dict('records')),"totalRows":len(value)})
        else:tables.append({"name":str(name),"value":clean(value)})
    for name,value in scope.get('plots',{}).items():
        s=pd.Series(value);indices=np.unique(np.linspace(0,len(s)-1,min(500,len(s))).astype(int)) if len(s) else []
        plots.append({"name":str(name),"values":[{"x":str(s.index[i]),"y":clean(s.iloc[i])} for i in indices]})
    return {"tables":tables,"plots":plots}


def run(payload):
    if payload.get('action')=='batch':
        from batch_research import run_batch
        return clean(run_batch(payload,run,clean))
    if payload.get('action')=='scan':
        from factor_scanner import scan
        return clean(scan(payload,metrics,clean))
    if payload.get('action')=='factor':
        if payload.get('tools',{}).get('protocol')=='robust_v2':from factor_research import research
        else:from factors import research
        return clean(research(payload,metrics,backtest))
    started=time.perf_counter();timings={}
    source=payload['source'];dataset=payload['dataset'];raw=payload['config'];params=payload.get('params',{});grid=payload.get('grid',{});settings=payload.get('tools',{})
    quick=settings.get('quick',False)
    if quick:grid={}
    def progress(value,message):print(f'進度 {value}% · {message}',flush=True)
    def timed(name):timings[name]=round(time.perf_counter()-started,4)
    progress(5,'準備行情與 K 線週期')
    if len(source)>100000:raise ValueError('Python 原始碼最多 100 KB。')
    if not isinstance(params,dict) or not isinstance(grid,dict):raise ValueError('params 與 grid 必須是 JSON 物件。')
    np.random.seed(int(settings.get('mc',{}).get('seed',42)))
    frame,frame_info=bar_periods.prepare(dataset,raw)
    timed('dataReadySeconds')
    if payload.get('action')=='code':return {"kind":"code","outputs":code_outputs(namespace(source,frame,params)),"environment":{"python":platform.python_version(),"numpy":np.__version__,"pandas":pd.__version__},"sourceHash":hashlib.sha256(source.encode()).hexdigest()}
    hourly=frame_info['timeframeMinutes']<1440
    if dataset['quality']['status']!='PASSED' or (not hourly and frame_info['sourceMinutes']==1440 and dataset['identity']['calendar']!='continuous'):raise ValueError('需通過品質驗證的 UTC 日線或 1H 行情。')
    c={"capital":100000,"allocation":.95,"commissionBps":5,"slippageBps":3,"spreadBps":2,"lotSize":.000001,"split":.65,"minTrades":3,"minMonths":6,"maxDrawdown":.25,**raw}
    meta_scope=namespace(source,frame,params);meta=meta_scope.get('STRATEGY_META',{})
    execution.validate(c)
    if c.get('executionMode','legacy')!='legacy' and not (callable(meta_scope.get('on_bar')) and callable(meta_scope.get('features'))):raise ValueError('OHLC 進階成交目前適用 features / on_bar 事件式 CFD 策略。請切換事件範本，或選相容模式。')
    c.update(barHours=frame_info['timeframeMinutes']/60,barMinutes=frame_info['timeframeMinutes'],timeframeMinutes=frame_info['timeframeMinutes'],calendarVerified=not hourly and frame_info['sourceMinutes']==1440,pointValue=raw.get('pointValue',1),marginRate=raw.get('marginRate',1),priceSide=dataset['identity'].get('priceSide','UNKNOWN'))
    time_rules.validate(c)
    if not isinstance(c.get('spreadPoints',0),(int,float)) or not math.isfinite(c.get('spreadPoints',0)) or not 0<=c.get('spreadPoints',0)<=1000:raise ValueError('spreadPoints 須為 0–1000。')
    if meta.get('timeframe') and frame_info['timeframeMinutes']!=bar_periods.minutes(meta['timeframe']) and not (raw.get('timeframeOverride',False) and hourly):raise ValueError('此策略指定 '+meta['timeframe']+'；請匯入符合策略指定週期的 OHLCV。')
    if meta.get('symbolContains') and not any(s in dataset['identity']['symbol'].upper() for s in meta['symbolContains']):raise ValueError('行情商品與策略指定商品不符。')
    for name,low,high in [('capital',100,1e10),('allocation',.01,1),('commissionBps',0,200),('slippageBps',0,200),('spreadBps',0,200),('lotSize',1e-8,1e6),('split',.5,.8),('minTrades',1,1000),('minMonths',1,120),('maxDrawdown',.01,.9)]:
        if not isinstance(c[name],(int,float)) or not math.isfinite(c[name]) or not low<=c[name]<=high:raise ValueError(f'{name} 超出合法範圍。')
    split=int(len(frame)*c['split']);gap=int(settings.get('gapBars',0));oos_start=split+gap
    if gap<0 or gap>250 or split<60 or len(frame)-oos_start<30:raise ValueError('IS/OOS 切分需至少 60 / 30 根，gap 為 0–250。')
    selector=settings.get('selector','sharpe')
    if selector not in ['sharpe','plateau']:raise ValueError('未知 selector。')
    progress(15,'IS 選參與指標計算')
    if grid:
        selected,winner,trials,pp=select_trials(source,frame,c,params,grid,0,split,selector,settings.get('search',{}))
    else:selected=params;winner='candidate-001';pp=plateau([],{});trials=[]
    blocked=bool(grid and winner is None)
    if blocked:model=None;dev_signals=np.zeros(split);signals=np.zeros(len(frame));pp['status']='NO_ELIGIBLE_CANDIDATE_CASH'
    else:
        model=fit_model(source,frame.iloc[:split],selected)
        dev_signals=signals_for(source,frame.iloc[:split],selected,model);signals=signals_for(source,frame,selected,model)
    development=backtest(frame.iloc[:split],dev_signals,c,0,split)
    if not grid:trials=[dict(trialId='candidate-001',params=selected,evidence='DEVELOPMENT',eligible=development['metrics']['tradeCount']>=c['minTrades'] and development['metrics']['sharpe'] is not None,metrics=development['metrics'],status='COMPLETED')]
    progress(35,'OOS、基準與全期間回測')
    evaluation=backtest(frame,signals,c,oos_start);baseline=backtest(frame,np.ones(len(frame)),c,oos_start)
    timed('primaryBacktestsSeconds')
    progress(50,'因果檢查' if not quick else '整理快速回測報告')
    audit=causal_audit(source,frame,selected,model,oos_start,signals,c) if not quick and not blocked else dict(status='NOT_RUN',checks=[],limitation='沒有合格選參時保留現金；快速模式或未選出策略時不執行因果檢查。')
    stress=[]
    progress(65,'成本與延遲壓力測試' if not quick else '計算風險指標')
    if not quick and settings.get('stress',True):
        for multiplier,delay in [(1,1),(2,1),(3,1),(1,2),(2,2)]:
            test_c={**c,**{k:c.get(k,0)*multiplier for k in ['commissionBps','slippageBps','spreadBps','spreadPoints','slippageTicks','commissionPerLot','minimumCommission','annualFinancingPercent'] if k in c or k in ['commissionBps','slippageBps','spreadBps','spreadPoints']}}
            result=backtest(frame,signals,test_c,oos_start,delay=delay,record=False)
            stress.append({"costMultiplier":multiplier,"executionDelayBars":delay,"metrics":result['metrics'],"reconciliationError":result['reconciliationError'],**({'executionAudit':result['executionAudit']} if 'executionAudit' in result else {})})
    primary_stress=next((s for s in stress if s['costMultiplier']==2 and s['executionDelayBars']==1),None)
    m=evaluation['metrics'];gates=[{"name":"樣本量","pass":m['tradeCount']>=c['minTrades'] and m['completeMonths']>=c['minMonths'],"detail":f"{m['tradeCount']} / {c['minTrades']} 筆交易 · {m['completeMonths']} / {c['minMonths']} 完整月"},{"name":"觀察 K 線收盤回撤","pass":m['maxDrawdown']<=c['maxDrawdown'],"detail":f"{m['maxDrawdown']:.2%} ≤ {c['maxDrawdown']:.0%}"},{"name":"完整月獲利比例","pass":m['positiveMonthRatio']>=.5 if m['positiveMonthRatio'] is not None else None,"detail":f"{m['positiveMonths']} / {m['completeMonths']}"},{"name":"因果抽樣檢查","pass":None if audit['status']=='NOT_RUN' else audit['status']=='PASS_SAMPLED',"detail":audit['status']}]
    result={"kind":"research","development":development,"evaluation":evaluation,"baseline":baseline,"stress":{"metrics":primary_stress['metrics'] if primary_stress else m,"reconciliationError":primary_stress['reconciliationError'] if primary_stress else evaluation['reconciliationError']},"trials":trials,"winner":winner,"parameters":selected,"plateau":pp,"causality":audit,"stressTests":stress,"gates":gates,"splitIndex":oos_start,"isEndIndex":split,"config":c,"environment":{"python":platform.python_version(),"numpy":np.__version__,"pandas":pd.__version__},"statistics":distribution_tools(evaluation.get('dailyNav',evaluation['nav']))}
    if grid:
        from parameter_search import selection_bias
        result['selectionBias']=selection_bias(trials,[x['return'] for x in development.get('dailyNav',development['nav'])],development['metrics']['sharpe'])
    if hourly:
        result['full']=backtest(frame,signals,c)
        result['fullBaseline']=backtest(frame,np.ones(len(frame)),c)
        result['executionModel']=evaluation.get('executionModel')
    progress(80,'Monte Carlo 與 Walk-forward' if not quick else '產生圖表與報告')
    if not quick and settings.get('mc',{}).get('enabled',True) and not evaluation['metrics'].get('insolvent'):result['monteCarlo']=monte_carlo(evaluation.get('dailyNav',evaluation['nav']),c['capital'],settings.get('mc',{}))
    if not quick and settings.get('walkForward',{}).get('enabled',False):result['walkForward']=walk_forward(source,frame,c,params,grid,settings['walkForward'],selector)
    result['outputs']=code_outputs(namespace(source,frame,selected)) if not blocked else {'tables':[],'plots':[]}
    frame_info.update(isEnd=frame.index[split-1].isoformat(),oosStart=frame.index[oos_start].isoformat())
    result['frameInfo']=frame_info;result['workerVersion']=VERSION
    delta=np.diff(frame.index.as_unit('ms').asi8)/3600000;expected=c['barHours'];gaps=delta[delta>expected+1e-8]
    result['dataAudit']=dict(status='STRUCTURE_PASSED_CALENDAR_UNVERIFIED' if not c['calendarVerified'] else 'STRUCTURE_PASSED',rows=len(frame),sourceRows=frame_info['sourceRows'],sourceTimeframeMinutes=frame_info['sourceMinutes'],timeframeMinutes=c['timeframeMinutes'],gapCount=len(gaps),maxGapHours=float(gaps.max()) if len(gaps) else 0.,zeroVolumeBars=int((frame.volume==0).sum()),priceSide=c['priceSide'],synthetic=dataset['identity'].get('synthetic',False),calendarVerified=c['calendarVerified'],source=dataset['identity'].get('source',''),symbol=dataset['identity'].get('symbol',''),start=frame.index[0].isoformat(),end=frame.index[-1].isoformat(),droppedIncompleteBuckets=frame_info.get('droppedIncompleteBuckets',0),scope='Gaps are observations, not proven missing exchange sessions. Volume may be quote liquidity. No OHLC interpolation; no broker quote/tick or calendar certification.')
    result['market']=[dict(date=t.isoformat(),**row) for t,row in frame.tail(240).to_dict('index').items()]
    timed('computeSeconds');result['performance']={**timings,'mode':'QUICK' if quick else 'FULL','bars':len(frame)}
    progress(95,'保存回測結果')
    return clean(result)


if __name__=='__main__':
    input_path,output_path=sys.argv[1:3]
    try:
        with open(input_path,encoding='utf-8') as f:payload=json.load(f)
        print(f'Python {platform.python_version()} · NumPy {np.__version__} · pandas {pd.__version__}',flush=True)
        print('執行本機程式；此程序不是安全沙箱。',flush=True)
        result=run(payload)
        with open(output_path,'w',encoding='utf-8') as f:f.write(json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(',',':')))
        print('COMPLETED · 結果已寫入研究紀錄。',flush=True)
    except BaseException:
        traceback.print_exc();sys.exit(1)
