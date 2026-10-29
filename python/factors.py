"""Causal price/volume factor research; IS-only selection, next-open investing."""
import math
import platform
import numpy as np
import pandas as pd
import pyramiding
import time_rules
import bar_periods

NAMES = {'momentum_short':'短期動能','momentum_medium':'中期動能','momentum_long':'長期動能','reversal':'短期反轉','trend_gap':'均線乖離','low_volatility':'低波動','low_range':'低振幅','drawdown':'距離近期高點','breakout':'通道位置','volume_surprise':'量能偏離','volume_price':'量價動能','intrabar_strength':'K 線強度'}


def features(f, s):
    a,b,c=[int(s.get(k,v)) for k,v in [('shortWindow',5),('mediumWindow',20),('longWindow',60)]]
    if not 2<=a<b<c<=500: raise ValueError('因子視窗需 2 ≤ 短期 < 中期 < 長期 ≤ 500。')
    close=f.close;ret=close.pct_change(fill_method=None);spread=f.high-f.low
    volume=f.volume.replace(0,np.nan)
    return pd.DataFrame({'momentum_short':close.pct_change(a,fill_method=None),'momentum_medium':close.pct_change(b,fill_method=None),'momentum_long':close.pct_change(c,fill_method=None),'reversal':-close.pct_change(a,fill_method=None),'trend_gap':close/close.rolling(b).mean()-1,'low_volatility':-ret.rolling(b).std(ddof=1),'low_range':-(spread/close).rolling(b).mean(),'drawdown':close/close.rolling(c).max()-1,'breakout':(close-f.low.rolling(b).min())/(f.high.rolling(b).max()-f.low.rolling(b).min()).replace(0,np.nan),'volume_surprise':volume/volume.rolling(b).mean()-1,'volume_price':(ret*volume/volume.rolling(b).mean()).rolling(a).mean(),'intrabar_strength':((close-f.open)/spread.replace(0,np.nan)).rolling(a).mean()},index=f.index).replace([np.inf,-np.inf],np.nan)


def rank_ic(x,y):
    pair=pd.concat([pd.Series(x).reset_index(drop=True),pd.Series(y).reset_index(drop=True)],axis=1).dropna()
    if len(pair)<3 or pair.iloc[:,0].nunique()<2 or pair.iloc[:,1].nunique()<2: return None
    return float(np.corrcoef(pair.iloc[:,0].rank(),pair.iloc[:,1].rank())[0,1])


def stats(x,y,edges=None):
    valid=x.notna()&y.notna();xx=x[valid];yy=y[valid]
    if edges is None:
        edges=np.unique(xx.quantile([.2,.4,.6,.8]).to_numpy()) if len(xx) else np.array([])
    bins=np.searchsorted(edges,xx.to_numpy(),side='right') if len(edges) else np.zeros(len(xx),dtype=int)
    groups=[dict(group=i+1,n=int((bins==i).sum()),meanReturn=float(yy[bins==i].mean()) if (bins==i).any() else None) for i in range(len(edges)+1)]
    blocks=[rank_ic(xx.iloc[i:i+60],yy.iloc[i:i+60]) for i in range(0,len(xx)-29,60)]
    blocks=[v for v in blocks if v is not None];sd=np.std(blocks,ddof=1) if len(blocks)>1 else 0
    return dict(n=len(xx),ic=rank_ic(xx,yy),groups=groups,spread=groups[-1]['meanReturn']-groups[0]['meanReturn'] if len(groups)>1 and groups[-1]['meanReturn'] is not None and groups[0]['meanReturn'] is not None else None,blockIC=blocks,blockICIR=float(np.mean(blocks)/sd) if sd>1e-12 else None),edges


def portfolio(frame_list, score, selected_names, s, start, metric_fn):
    """Fractional unlevered units; intersected observed timestamps, no price ffill."""
    n=len(frame_list[0]);count=len(frame_list);capital=float(s['capital']);cash=capital;q=np.zeros(count);nav=[];fills=[];rebalances=[];peak=previous=capital;fees=turnover=0.
    top=int(s.get('topAssets',2));every=int(s.get('rebalanceBars',24))
    if not 1<=top<=count or not 1<=every<=1000: raise ValueError('持有商品數或再平衡根數無效。')
    weight_mode=s.get('weighting','equal')
    if weight_mode not in ['equal','inverse_volatility']:raise ValueError('未知組合權重法。')
    price=np.column_stack([f.open for f in frame_list]);close=np.column_stack([f.close for f in frame_list]);vol=np.column_stack([f.close.pct_change(fill_method=None).rolling(20).std(ddof=1) for f in frame_list]);rate=(s['commissionBps']+s['slippageBps']+s['spreadBps']/2)/10000
    for i in range(start,n):
        current=q*price[i];equity=cash+current.sum();cost=0.;held=bool(q.sum())
        if (i-start)%every==0:
            lag=i-1;values=score[lag];eligible=np.flatnonzero(np.isfinite(values))
            chosen=sorted(eligible,key=lambda j:(-values[j],selected_names[j]))[:top]
            w=np.zeros(count)
            if chosen:
                v=np.ones(len(chosen)) if weight_mode=='equal' else 1/np.maximum(vol[lag,chosen],1e-6)
                if not np.all(np.isfinite(v)):raise ValueError('逆波動權重缺少前序波動資料。')
                w[chosen]=v/v.sum()*.95
            # Solve post-cost NAV before assigning target notionals; no hidden cash injection.
            lo=0.;hi=equity
            for _ in range(55):
                mid=(lo+hi)/2;required=mid+rate*np.abs(mid*w-current).sum()
                if required>equity:hi=mid
                else:lo=mid
            target=lo*w;delta=target-current;notional=float(np.abs(delta).sum());cost=notional*rate;cash=equity-target.sum()-cost;q=target/price[i];fees+=cost;turnover+=notional/equity
            decision=(frame_list[0].index[lag]+pd.Timedelta(hours=s['barHours'])).isoformat()
            rebalances.append(dict(time=frame_list[0].index[i].isoformat(),decisionTime=decision,turnover=notional/equity,cost=cost,weights={selected_names[j]:float(w[j]) for j in chosen},scores={selected_names[j]:float(values[j]) for j in chosen}))
            for j in np.flatnonzero(np.abs(delta)>1e-8):fills.append(dict(time=frame_list[0].index[i].isoformat(),decisionTime=decision,featureAvailableTime=decision,symbol=selected_names[j],side='BUY' if delta[j]>0 else 'SELL',quantity=abs(float(delta[j]/price[i,j])),reference=float(price[i,j]),notional=abs(float(delta[j])),cost=abs(float(delta[j]))*rate))
        value=float(cash+(q*close[i]).sum());held=held or bool(q.sum())
        if i==n-1:
            liquidation=float((q*close[i]).sum());exit_fee=liquidation*rate;cost+=exit_fee;fees+=exit_fee;turnover+=liquidation/value
            for j in np.flatnonzero(q>0):fills.append(dict(time=(frame_list[0].index[i]+pd.Timedelta(hours=s['barHours'])).isoformat(),symbol=selected_names[j],side='SELL',quantity=float(q[j]),reference=float(close[i,j]),notional=float(q[j]*close[i,j]),cost=float(q[j]*close[i,j]*rate),reason='END_OF_PERIOD'))
            cash=value-exit_fee;q[:]=0;value=cash
        if value<=0 or cash < -1e-6:raise ValueError('組合資金耗盡或超支。')
        commission=cost*s['commissionBps']/10000/rate if rate else 0.
        peak=max(peak,value);nav.append(dict(date=frame_list[0].index[i].isoformat(),nav=value,cash=float(cash),quantity=float(q.sum()),**{'return':value/previous-1},pnl=value-previous,drawdown=1-value/peak,commission=commission,executionDrag=cost-commission,hadPosition=held));previous=value
    daily=pyramiding.daily_nav(nav,capital,timezone=s['tradingTimezone']);m=metric_fn(daily,[],s);m.update(maxDrawdown=max(x['drawdown'] for x in nav),totalCost=fees,turnover=turnover,rebalanceCount=len(rebalances),tradeCount=len(fills),netPnl=nav[-1]['nav']-capital)
    m['calmar']=m['cagr']/m['maxDrawdown'] if m['cagr'] is not None and m['maxDrawdown'] else None
    m.update(pyramiding.recovery_metrics(nav,capital,m['netPnl']))
    return dict(nav=nav,dailyNav=daily,metrics=m,fills=fills,rebalances=rebalances,reconciliationError=(nav[-1]['nav']-capital)-sum(x['pnl'] for x in nav),scope='95% long-only fractional-unit price-return portfolio; 5% cash reserve. IS-only factor directions/selection. Previous completed close ranks -> next common observed open. All traded notional charged commission + slippage + half spread, including final liquidation. No financing, dividends, taxes, FX or CFD margin model. Sampled universe may have survivorship bias.')


def research(payload,metric_fn,backtest_fn):
    datasets=payload['datasets'];s={'capital':100000.,'split':.65,'commissionBps':5.,'slippageBps':3.,'spreadBps':2.,'lotSize':.000001,'allocation':.95,'minTrades':3,'minMonths':6,'maxDrawdown':.25,**payload.get('config',{})}
    for key,low,high in [('capital',100,1e10),('split',.5,.8),('commissionBps',0,200),('slippageBps',0,200),('spreadBps',0,200)]:
        if type(s[key]) not in [int,float] or not math.isfinite(s[key]) or not low<=s[key]<=high:raise ValueError(key+' 超出合法範圍。')
    if not 1<=len(datasets)<=20:raise ValueError('請選擇 1–20 個資料集。')
    identities=[d['identity'] for d in datasets];symbols=[d['symbol'] for d in identities];mode=payload.get('tools',{}).get('mode','single')
    if mode not in ['single','portfolio']:raise ValueError('未知因子模式。')
    if len(set(symbols))!=len(symbols):raise ValueError('同一商品請只選一份資料；不可重複計算資產。')
    if mode=='single' and len(datasets)!=1:raise ValueError('時序研究請選一個商品。')
    if mode=='portfolio' and len(datasets)<3:raise ValueError('橫斷面因子組合需要至少 3 個不同商品；不能以單一商品當作橫斷面樣本。')
    for k in ['timeframe','currency','synthetic']:
        if len({d.get(k,False) for d in identities})!=1:raise ValueError('組合須使用相同週期、幣別與資料類型（合成或真實）。')
    s.update(barHours=int(s.get('timeframeMinutes',bar_periods.minutes(identities[0]['timeframe'])))/60,calendarVerified=False,priceSide=identities[0].get('priceSide','UNKNOWN'))
    time_rules.validate(s)
    if s['sessionEnabled']:raise ValueError('因子研究目前使用全時段；進場時段濾網請於 Python 策略工作台設定。')
    frames=[];input_rows=[]
    for d in datasets:
        if d['quality']['status']!='PASSED':raise ValueError('因子資料需先通過結構品質驗證。')
        f,info=bar_periods.prepare(d,s);frames.append(f);input_rows.append(len(f))
    # Features use each asset's own observed history before common timestamp alignment.
    feat=[features(f,s) for f in frames];common=frames[0].index
    for f in frames[1:]:common=common.intersection(f.index)
    common=common.sort_values();frames=[f.loc[common] for f in frames];feat=[f.loc[common] for f in feat];n=len(common);split=int(n*s['split']);h=int(s.get('horizon',5))
    if h not in [1,5,10,20] or split<100 or n-split<50:raise ValueError('因子研究需至少 100 IS / 50 OOS 共同觀察根；預測期為 1/5/10/20。')
    names=payload.get('tools',{}).get('factors',list(NAMES))
    if not isinstance(names,list) or not names or len(names)>12 or any(k not in NAMES for k in names) or len(names)!=len(set(names)):raise ValueError('請選擇有效且不重複的內建因子。')
    # F_t -> enter O_(t+1), leave O_(t+h+1). Labels touching split are purged from IS.
    labels=[f.open.shift(-(h+1))/f.open.shift(-1)-1 for f in frames];train_end=split-h-1
    rows=[];directions={};edges={};correlation_data={}
    for name in names:
        x=feat[0][name];y=labels[0]
        if mode=='single':
            train,e=stats(x.iloc[:train_end],y.iloc[:train_end]);test,_=stats(x.iloc[split:],y.iloc[split:],e);edges[name]=e;correlation_data[name]=x.iloc[:train_end].reset_index(drop=True)
        else:
            xs=pd.DataFrame({j:f[name] for j,f in enumerate(feat)},index=common);ys=pd.DataFrame({j:y for j,y in enumerate(labels)},index=common)
            xr=xs.rank(axis=1,pct=True);yr=ys.rank(axis=1,pct=True);valid=xs.notna().all(axis=1)&ys.notna().all(axis=1)
            z=(xr.sub(xr.mean(axis=1),axis=0)*yr.sub(yr.mean(axis=1),axis=0)).sum(axis=1)/(np.sqrt(xr.sub(xr.mean(axis=1),axis=0).pow(2).sum(axis=1)*yr.sub(yr.mean(axis=1),axis=0).pow(2).sum(axis=1)))
            z=z.where(valid).replace([np.inf,-np.inf],np.nan)
            def cs_stat(part):
                vals=z.iloc[part].dropna();xx=xr.iloc[part].to_numpy().ravel();yy=ys.iloc[part].to_numpy().ravel();a,_=stats(pd.Series(xx),pd.Series(yy),np.array([.2,.4,.6,.8]));a.update(ic=float(vals.mean()) if len(vals) else None,n=len(vals),blockIC=[],blockICIR=float(vals.mean()/vals.std(ddof=1)) if len(vals)>1 and vals.std(ddof=1)>1e-12 else None);return a
            train=cs_stat(slice(0,train_end));test=cs_stat(slice(split,n));correlation_data[name]=pd.Series(xr.iloc[:train_end].to_numpy().ravel())
        direction=1 if train['ic'] is None or train['ic']>=0 else -1;directions[name]=direction
        rows.append(dict(name=name,label=NAMES[name],direction=direction,development=train,evaluation=test))
    corr=pd.DataFrame(correlation_data).rank().corr()
    choose=int(s.get('selectedFactors',3));threshold=float(s.get('correlationLimit',.85))
    if not 1<=choose<=12 or not .1<=threshold<=1:raise ValueError('選因子數為 1–12，相關性門檻為 0.1–1。')
    candidates=sorted([r for r in rows if r['development']['ic'] is not None and r['development']['n']>=30],key=lambda r:(-abs(r['development']['ic']),r['name']));selected=[]
    for r in candidates:
        if len(selected)>=choose:break
        if all(pd.isna(corr.loc[r['name'],k]) or abs(corr.loc[r['name'],k])<threshold for k in selected):selected.append(r['name'])
    if not selected:raise ValueError('沒有足夠 IS 樣本的有效因子；請增加資料或縮短視窗。')
    if mode=='single':
        composite=pd.Series(0.,index=common);valid=pd.Series(True,index=common)
        for name in selected:
            train=feat[0][name].iloc[:train_end].dropna().sort_values().to_numpy();value=feat[0][name];rank=pd.Series(np.searchsorted(train,value)/len(train),index=common)
            valid &= value.notna();composite+=(rank if directions[name]>0 else 1-rank)/len(selected)
        entry=float(s.get('scoreThreshold',.6))
        if not .05<=entry<=.95:raise ValueError('組合分數進場門檻為 0.05–0.95。')
        signal=((composite>=entry)&valid).astype(int).to_numpy();evaluation=backtest_fn(frames[0],signal,s,split);baseline=backtest_fn(frames[0],np.ones(n),s,split)
        current=[dict(symbol=symbols[0],score=float(composite.iloc[-1]) if valid.iloc[-1] else None,target='LONG' if signal[-1] else 'CASH')]
    else:
        score=np.zeros((n,len(frames)))*np.nan;parts=[]
        for name in selected:
            v=pd.DataFrame({j:f[name] for j,f in enumerate(feat)}).rank(axis=1,pct=True).to_numpy();parts.append(v if directions[name]>0 else 1-v)
        score=np.mean(parts,axis=0);evaluation=portfolio(frames,score,symbols,s,split,metric_fn);baseline=portfolio(frames,np.ones_like(score),symbols,{**s,'topAssets':len(frames)},split,metric_fn)
        current=[dict(symbol=symbols[j],score=float(score[-1,j]) if np.isfinite(score[-1,j]) else None) for j in sorted(range(len(frames)),key=lambda j:(-np.nan_to_num(score[-1,j],nan=-1e10),symbols[j]))]
    for section in [evaluation,baseline]:
        if mode=='single':
            daily=pyramiding.daily_nav(section['nav'],s['capital'],timezone=s['tradingTimezone']);section['dailyNav']=daily
            section['metrics']=metric_fn(daily,section['trades'],s)
            section['metrics']['maxDrawdown']=max(x['drawdown'] for x in section['nav'])
            m=section['metrics'];m['calmar']=m['cagr']/m['maxDrawdown'] if m['cagr'] is not None and m['maxDrawdown'] else None
        section['metrics'].update(pyramiding.recovery_metrics(section['nav'],s['capital'],section['metrics']['netPnl']))
        # Exact timestamp intersection does not certify session completeness.
        for month in section['metrics']['monthly']:month['complete']=False
        section['metrics'].update(completeMonths=0,positiveMonths=0,positiveMonthRatio=None)
    # Prefix invariance verifies the feature builder; fitted IS selection remains frozen for OOS.
    cut=max(100,n//2);audit=all(np.allclose(features(original.iloc[:original.index.searchsorted(common[cut])],s).to_numpy(),features(original,s).iloc[:original.index.searchsorted(common[cut])].to_numpy(),equal_nan=True) for original in [pd.DataFrame(d['bars']).set_index(pd.to_datetime([b['time'] for b in d['bars']],unit='ms',utc=True)).drop(columns='time') for d in datasets])
    horizon_ic=[]
    for period in [1,5,10,20]:
        # Diagnostic only: never reselect direction or weights using OOS or alternate horizons.
        if mode=='single':
            y=frames[0].open.shift(-(period+1))/frames[0].open.shift(-1)-1
            horizon_ic.append(dict(horizon=period,values={k:rank_ic(feat[0][k].iloc[split:],y.iloc[split:]) for k in selected}))
    return dict(kind='factor',mode=mode,config=s,environment=dict(python=platform.python_version(),numpy=np.__version__,pandas=pd.__version__),factors=rows,selected=selected,correlation=dict(names=names,values=corr.to_numpy().tolist()),current=current,horizonIC=horizon_ic,evaluation=evaluation,baseline=baseline,quality=dict(dataIdentity=[{k:d.get(k) for k in ['symbol','source','timeframe','currency','priceSide','proxyFor']} for d in identities],volumeWarning='Volume semantics depend on provider. Dukascopy CFD volume is quote-side liquidity, not centralized NASDAQ traded volume. Verify adjustment and corporate actions before cross-asset research.',inputRows=input_rows,alignedRows=n,droppedRows=[r-n for r in input_rows],alignment='Exact intersection of UTC bar OPEN timestamps. Features built on each own history first. No forward-filled prices; portfolio holds across gaps between common observed timestamps.',start=common[0].isoformat(),end=common[-1].isoformat(),oosStart=common[split].isoformat(),purgedISRows=h+1,symbols=symbols,synthetic=bool(identities[0].get('synthetic'))),causality=dict(status='PASS_PREFIX' if audit else 'FAILED',scope='Built-in feature prefix invariance; no claim of exhaustive causal proof.'),methodology=dict(selection='IS absolute Rank IC descending; greedily drop correlated factors, equal composite weight. Direction determined ONLY by IS. OOS is KNOWN_EVALUATION.',ic='Single: Spearman correlation over time, NOT cross-sectional IC. Portfolio: mean per-timestamp cross-sectional Spearman (>=3 assets). ICIR unannualized; no significance claim.',labels=f'Factor at close t, forward OPEN(t+{h+1}) / OPEN(t+1) - 1; IS labels ending at/after OOS are purged.',groups='Single quantile edges fitted only on IS and frozen for OOS. Portfolio groups use contemporaneous cross-sectional percentile ranks. Group returns are overlapping GROSS label averages, not tradable portfolio PnL.',limits='12 technical price/volume factors only; no fundamental/value/quality data. Multiple testing, overlapping labels, serial dependence and universe survivorship are not significance-adjusted. Reported IC is exploratory. Hourly session completeness unverified; no return guarantee.'),evidence='KNOWN_EVALUATION',activation_state='INACTIVE')
