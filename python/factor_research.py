"""V2 technical factor protocol: frozen chronological training, HAC/FDR and net investing."""
import json,math,platform
from pathlib import Path
import numpy as np
import pandas as pd
import bar_periods,time_rules
from factors import features,rank_ic,NAMES
from factor_statistics import segment_stats,adjust_fdr,ecdf_score
from factor_portfolio import simulate
from factor_evidence import evidence,weights,logic_plan,python_draft

def settings(payload):
    s=dict(capital=100000.,trainFraction=.6,validationFraction=.2,horizon=5,embargoBars=5,shortWindow=5,mediumWindow=20,longWindow=60,selectedFactors=3,correlationLimit=.85,coverageMinimum=.8,fdrMethod='BY',fdrAlpha=.1,icBlockSize=60,minimumICObservations=20,winsorQuantile=.01,allocation=.95,maxAssetWeight=.35,rebalanceBars=24,topAssets=3,weighting='equal',rankBuffer=1,rebalanceBand=.02,maxTargetTurnover=1.,scoreThreshold=.6,exitThreshold=.5,commissionBps=5.,slippageBps=3.,spreadBps=2.,lotSize=.000001,minTrades=3,minMonths=6,maxDrawdown=.25,tradingTimezone='UTC',sessionEnabled=False)
    s.update(payload.get('config',{}))
    for key,lo,hi in [('capital',100,1e10),('trainFraction',.4,.75),('validationFraction',.1,.3),('coverageMinimum',.3,1),('fdrAlpha',.001,.25),('correlationLimit',.1,1),('allocation',.05,.95),('maxAssetWeight',.05,.95),('rebalanceBand',0,.2),('maxTargetTurnover',.01,2),('scoreThreshold',.05,.95),('exitThreshold',.01,.9),('winsorQuantile',0,.1),('commissionBps',0,200),('slippageBps',0,200),('spreadBps',0,200)]:
        if type(s[key]) not in (int,float) or not math.isfinite(s[key]) or not lo<=s[key]<=hi:raise ValueError(key+' 超出合法範圍。')
    for key,lo,hi in [('horizon',1,20),('embargoBars',0,500),('selectedFactors',1,12),('icBlockSize',30,500),('minimumICObservations',20,1000),('rebalanceBars',1,1000),('topAssets',1,20),('rankBuffer',0,20)]:
        if type(s[key]) is not int or not lo<=s[key]<=hi:raise ValueError(key+' 必須為合法整數。')
    if s['horizon'] not in [1,5,10,20] or s['trainFraction']+s['validationFraction']>.9:raise ValueError('請保留至少 10% 最終測試資料；預測期限 1/5/10/20。')
    if s['exitThreshold']>=s['scoreThreshold']:raise ValueError('退場分數必須小於進場分數。')
    if s['fdrMethod'] not in ['BY','BH','none'] or s['weighting'] not in ['equal','inverse_volatility']:raise ValueError('未知 FDR 或權重方法。')
    return s

def research(payload,metric_fn,backtest_fn=None):
    print('進度 5% · 資料與研究契約',flush=True)
    s=settings(payload);datasets=payload['datasets'];mode=payload.get('tools',{}).get('mode','single');identities=[d['identity'] for d in datasets];symbols=[d['symbol'] for d in identities]
    if mode not in ['single','portfolio'] or not 1<=len(datasets)<=20:raise ValueError('未知因子研究模式。')
    if len(set(symbols))!=len(symbols):raise ValueError('同一商品不可重複計入組合。')
    if mode=='single' and len(datasets)!=1:raise ValueError('時序研究需選一個商品。')
    if mode=='portfolio' and len(datasets)<5:raise ValueError('嚴謹橫斷面研究至少需要 5 個不同商品；建議更大的 point-in-time universe。')
    for key in ['timeframe','currency','synthetic']:
        if len({i.get(key,False) for i in identities})!=1:raise ValueError('組合需相同週期、幣別及真實／合成類型。')
    s['barHours']=bar_periods.minutes(identities[0]['timeframe'])/60;s['calendarVerified']=False;time_rules.validate(s)
    if s['sessionEnabled']:raise ValueError('因子 V2 研究使用全時段。')
    original=[];feat=[];counts=[];source_quality=[]
    for d in datasets:
        q=d['quality'];observed=payload.get('tools',{}).get('calendarPolicy')=='observed_bars' and d['identity']['timeframe']=='1 Day' and q['status']=='REVIEW_REQUIRED' and q.get('duplicate',0)==0 and q.get('invalid',0)==0
        if q['status']!='PASSED' and not observed:raise ValueError('行情須先通過結構品質檢查；交易日日線可啟用原始觀察日曆研究，仍需核對休市與缺日。')
        source_quality.append(dict(status=q['status'],calendar=q.get('calendar'),calendarVerified=False,gapCount=q.get('gapCount',len(q.get('gaps',[]))),warnings=q.get('warnings',[]),calendarPolicy='OBSERVED_DAILY_REVIEW_REQUIRED' if observed else 'ORIGINAL_STRUCTURAL_CHECKS'))
        f,info=bar_periods.prepare(d,s);original.append(f);feat.append(features(f,s));counts.append(len(f))
    common=original[0].index
    for f in original[1:]:common=common.intersection(f.index)
    common=common.sort_values();frames=[f.loc[common] for f in original];feat=[f.loc[common] for f in feat];n=len(common);h=s['horizon'];b1=int(n*s['trainFraction']);b2=int(n*(s['trainFraction']+s['validationFraction']));purge=h+1
    plan=dict(trainStart=0,trainEnd=b1-purge,validationStart=b1+s['embargoBars'],validationEnd=b2-purge,testStart=b2+s['embargoBars'],testEnd=n,purgeBars=purge,embargoBars=s['embargoBars'])
    if plan['trainEnd']<240 or plan['validationEnd']-plan['validationStart']<60 or n-plan['testStart']<60:raise ValueError('分期後資料不足：至少 240 訓練、60 驗證及60測試根，另需暖機、purge及隔離。')
    names=payload.get('tools',{}).get('factors',list(NAMES))
    if not isinstance(names,list) or not names or len(names)>12 or len(set(names))!=len(names) or any(k not in NAMES for k in names):raise ValueError('請選不重複的內建因子。')
    labels=pd.DataFrame({j:f.open.shift(-(h+1))/f.open.shift(-1)-1 for j,f in enumerate(frames)},index=common)
    rows=[];inputs={};edges={};corr_data={}
    for i,name in enumerate(names):
        print(f'進度 {10+int(35*i/len(names))}% · 檢驗 {NAMES[name]}',flush=True)
        x=pd.DataFrame({j:f[name] for j,f in enumerate(feat)},index=common);inputs[name]=x
        train,e=segment_stats(x,labels,mode,rank_ic,0,plan['trainEnd'],h,block_size=s['icBlockSize']);edges[name]=e
        validation,_=segment_stats(x,labels,mode,rank_ic,plan['validationStart'],plan['validationEnd'],h,e,block_size=s['icBlockSize'])
        test,_=segment_stats(x,labels,mode,rank_ic,plan['testStart'],n,h,e,block_size=s['icBlockSize'])
        decision_ic=train['inference']['mean'];direction=1 if decision_ic is None or decision_ic>=0 else -1
        train['globalRankIC']=train['ic'];train['ic']=decision_ic
        for part in [validation,test]:part['globalRankIC']=part['ic'];part['ic']=part['inference']['mean']
        series=train['icSeries'];stability=sum(v['value']*direction>0 for v in series)/len(series) if series else None
        rows.append(dict(name=name,label=NAMES[name],direction=direction,development=train,validation=validation,evaluation=test,trainingSignConsistency=stability))
        blocks=np.asarray(train['blockIC']);halves=np.array_split(blocks,2)
        rows[-1]['trainingHalfIC']=[float(z.mean()) if len(z) else None for z in halves]
        rows[-1]['stableTrainingDirection']=bool(len(blocks)>=s['minimumICObservations'] and all(len(z) and float(z.mean())*direction>0 for z in halves) and stability>=.55)
        rows[-1]['robustEvidence']=evidence(train['blockIC'],direction,42+i)
        corr_data[name]=x.iloc[:plan['trainEnd']].rank(axis=1,pct=True).to_numpy().ravel() if mode=='portfolio' else x.iloc[:plan['trainEnd'],0].to_numpy()
    corr=pd.DataFrame(corr_data).rank().corr();q=adjust_fdr([r['development']['inference']['p'] for r in rows],s['fdrMethod'])
    for row,adjusted in zip(rows,q):
        a=row['development'];inference=a['inference'];row['adjustedP']=adjusted
        row['selectionStatus']='LOW_COVERAGE' if a['coverage']<s['coverageMinimum'] else 'INSUFFICIENT_IC_OBSERVATIONS' if inference['n']<s['minimumICObservations'] else 'UNDEFINED_INFERENCE' if inference['p'] is None else 'FDR_REJECTED' if s['fdrMethod']!='none' and adjusted>s['fdrAlpha'] else 'CANDIDATE'
        if row['selectionStatus']=='CANDIDATE' and not row['stableTrainingDirection']:row['selectionStatus']='UNSTABLE_TRAINING_DIRECTION'
        if row['selectionStatus']=='CANDIDATE' and (row['robustEvidence'].get('forwardSignConsistency') or 0)<2/3:row['selectionStatus']='UNSTABLE_TRAINING_FOLDS'
    candidates=sorted([r for r in rows if r['selectionStatus']=='CANDIDATE'],key=lambda r:(r['adjustedP'],-abs(r['development']['ic']),r['name']));selected=[]
    for r in candidates:
        if len(selected)>=s['selectedFactors']:r['selectionStatus']='SELECTION_BUDGET';continue
        if any(pd.notna(corr.loc[r['name'],k]) and abs(corr.loc[r['name'],k])>=s['correlationLimit'] for k in selected):r['selectionStatus']='CORRELATED';continue
        selected.append(r['name']);r['selectionStatus']='SELECTED'
    directions={r['name']:r['direction'] for r in rows};factor_weights=weights(rows,selected,corr);parts=[]
    for name in selected:
        x=inputs[name]
        if mode=='single':v=ecdf_score(x.iloc[:plan['trainEnd'],0],x.iloc[:,0],s['winsorQuantile'])[:,None]
        else:v=x.rank(axis=1,pct=True).to_numpy()
        parts.append((v if directions[name]>0 else 1-v)*factor_weights[name])
    score=np.sum(parts,axis=0) if parts else np.full((n,len(frames)),np.nan)
    print('進度 55% · 凍結因子並計算成本後投資',flush=True)
    validation=simulate(frames,score,symbols,s,plan['validationStart'],plan['validationEnd'],metric_fn)
    validation_stress=simulate(frames,score,symbols,{**s,**{k:s[k]*2 for k in ['commissionBps','slippageBps','spreadBps']}},plan['validationStart'],plan['validationEnd'],metric_fn)
    validation_baseline=simulate(frames,np.ones_like(score),symbols,s,plan['validationStart'],plan['validationEnd'],metric_fn,True)
    evaluation=simulate(frames,score,symbols,s,plan['testStart'],n,metric_fn)
    baseline=simulate(frames,np.ones_like(score),symbols,s,plan['testStart'],n,metric_fn,True)
    gross=simulate(frames,score,symbols,{**s,'commissionBps':0.,'slippageBps':0.,'spreadBps':0.},plan['testStart'],n,metric_fn)
    stress=simulate(frames,score,symbols,{**s,**{k:s[k]*2 for k in ['commissionBps','slippageBps','spreadBps']}},plan['testStart'],n,metric_fn)
    for section in [validation,evaluation,baseline,gross,stress]:
        for month in section['metrics']['monthly']:month['complete']=False
        section['metrics'].update(completeMonths=0,positiveMonths=0,positiveMonthRatio=None)
    # Feature prefix check is independent of fitted full-history arrays.
    cut=common[b1];audit=all(np.allclose(features(f.loc[f.index<cut],s).to_numpy(),features(f,s).loc[f.index<cut].to_numpy(),equal_nan=True) for f in original)
    current=[dict(symbol=symbols[j],score=float(score[-1,j]) if np.isfinite(score[-1,j]) else None,target='觀察分數，非委託') for j in sorted(range(len(symbols)),key=lambda j:(-np.nan_to_num(score[-1,j],nan=-1e10),symbols[j]))]
    ready=dict(validationCalendarDays=float((common[plan['validationEnd']-1]-common[plan['validationStart']]).total_seconds()/86400),validationDailyObservations=len(validation.get('dailyNav',validation['nav'])),validationExcessReturn=validation['metrics']['totalReturn']-validation_baseline['metrics']['totalReturn'],doubleCostValidationNetPnl=validation_stress['metrics']['netPnl'],trainingStabilityRule='Training halves plus three expanding training-only IC windows; at least 2/3 forward blocks maintain their past-only direction.')
    blueprint=logic_plan(rows,selected,factor_weights,s,validation,validation_stress,validation_baseline,ready,common[b1-1].isoformat(),mode,identities[0])
    blueprint['trainingFeatureEnd']=common[plan['trainEnd']-1].isoformat()
    draft=python_draft(blueprint,s,identities[0])
    horizons=[]
    for hh in [1,5,10,20]:
        yy=pd.DataFrame({j:f.open.shift(-(hh+1))/f.open.shift(-1)-1 for j,f in enumerate(frames)},index=common)
        for row in rows:
            if hh==h:tr,va=row['development'],row['validation']
            else:
                tr,_=segment_stats(inputs[row['name']],yy,mode,rank_ic,0,min(plan['trainEnd'],b1-hh-1),hh,block_size=s['icBlockSize'])
                va,_=segment_stats(inputs[row['name']],yy,mode,rank_ic,plan['validationStart'],min(plan['validationEnd'],b2-hh-1),hh,block_size=s['icBlockSize'])
            horizons.append(dict(name=row['name'],horizon=hh,trainingIC=tr['inference']['mean'],validationIC=va['inference']['mean'],usedForSelection=hh==h,scope='Other horizons are exploratory diagnostics; no automatic horizon optimization.'))
    literature=json.loads((Path(__file__).resolve().parents[1]/'docs'/'factor-literature.json').read_text(encoding='utf-8'))
    split_dates={k:common[min(n-1,max(0,v-1 if k.endswith('End') else v))].isoformat() for k,v in plan.items() if k.endswith('Start') or k.endswith('End')}
    print('進度 95% · 帳戶核對與研究紀錄',flush=True)
    return dict(kind='factor',mode=mode,protocolVersion='FACTOR_RESEARCH_V4',config=s,environment=dict(python=platform.python_version(),numpy=np.__version__,pandas=pd.__version__),factors=rows,selected=selected,correlation=dict(names=names,values=corr.to_numpy().tolist()),current=current,horizonIC=horizons,logicPlan=blueprint,strategyDraft=draft,factorWeights=factor_weights,evaluation=evaluation,validation=validation,baseline=baseline,gross=gross,costStress=stress,validationStress=validation_stress,validationBaseline=validation_baseline,
      readiness=ready,
      protocol=dict(status='RESEARCH_ONLY' if selected else 'NO_FACTOR_PASSED_CASH',selection='TRAIN_ONLY_FROZEN',splitIndices=plan,splitDates=split_dates,testEvidence='KNOWN_EVALUATION_ON_DISPLAY',fdrMethod=s['fdrMethod'],fdrAlpha=s['fdrAlpha'],familySize=len(names),inference='Asymptotic HAC mean-IC test; BY default. Not a finite-sample discovery guarantee.',repeatedResearch='Within-run factor family only. Manual tuning and repeated runs are NOT adjusted.',sources=[r['id'] for r in literature['references']]),literature=literature,
      quality=dict(dataIdentity=[{k:i.get(k) for k in ['symbol','source','timeframe','currency','priceSide','proxyFor']} for i in identities],sourceQuality=source_quality,inputRows=counts,alignedRows=n,droppedRows=[v-n for v in counts],alignment='Exact common UTC open timestamps; features computed on each own history first. No forward fill. Observed daily gaps can be retained explicitly; exchange calendar and missing observations remain unverified.',start=common[0].isoformat(),end=common[-1].isoformat(),oosStart=common[plan['testStart']].isoformat(),purgedISRows=purge,embargoBars=s['embargoBars'],symbols=symbols,synthetic=bool(identities[0].get('synthetic')),universeStatus='STATIC_IMPORTED_UNIVERSE_NOT_POINT_IN_TIME_VERIFIED',volumeWarning='CFD quote-side volume is not exchange traded volume; fundamental availability, adjustments, delistings and survivorship remain unverified.',crossSectionNote='At least 5 assets; small universes still have coarse ranks and weak external validity.' if mode=='portfolio' else 'Single-instrument time-series signal study, not cross-sectional equity factor evidence.'),
      causality=dict(status='PASS_PREFIX' if audit else 'FAILED',scope='Feature prefix check; separate regression tests perturb validation/test prices and verify frozen training selection. No exhaustive causality proof.'),
      methodology=dict(selection='Training-only directions, winsorized ECDF, IC-fold stability, reliability weights and Spearman redundancy filter. No selection from validation or final test. No passing factors means CASH, without best-IC fallback.',ic='Time series: mean nonoverlapping block Spearman IC; cross-section: mean per-date Spearman with >=5 paired assets. HAC Bartlett SE, asymptotic normal p, BY/BH adjusted p. CIs are pointwise, not simultaneous.',labels=f'Close t -> OPEN(t+{h+1}) / OPEN(t+1)-1; h+1 rows purged BEFORE each boundary, additional embargo AFTER boundary.',investing=evaluation['scope'],groups='Frozen training quantiles for time-series, contemporaneous cross-sectional ranks for universe. Overlapping gross label averages, not tradable factor returns.',limits='12 technical factors only; no PIT fundamental value/quality factors, sector neutralization, delisting returns, market impact, live orders or certified institutional completeness. Data quality and repeated search can invalidate asymptotic inference.'),evidence='KNOWN_EVALUATION',activation_state='INACTIVE')
