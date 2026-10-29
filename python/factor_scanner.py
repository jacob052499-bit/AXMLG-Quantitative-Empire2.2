from atomic_file import atomic_json
"""Batch independent instruments; explicit research ranking, not a trade recommendation."""
import contextlib
import io
import json
import math
import hashlib
from pathlib import Path
import platform
import numpy as np
import pandas as pd
from factor_research import research, settings
from factor_statistics import adjust_fdr
from factors import NAMES

VERSION = 'FACTOR_SCAN_V3'
METRICS = ['netPnl','totalReturn','sharpe','maxDrawdown','maxDrawdownAmount','profitToMdd','tradeCount','winRate','profitFactor','avgTradePnl','totalCost','turnover','cagr']

def finite(v, default=0.):
    return float(v) if isinstance(v,(int,float)) and math.isfinite(v) else default

def strategy_style(factors):
    trend=reverse=filters=0
    for f in factors:
        if f['selectionStatus']!='SELECTED':continue
        if f['name'] in ['momentum_short','momentum_medium','momentum_long','trend_gap','drawdown','breakout']:
            if f['direction']>0:trend+=1
            else:reverse+=1
        elif f['name']=='reversal':
            if f['direction']>0:reverse+=1
            else:trend+=1
        else:filters+=1
    if trend>reverse:return '趨勢跟隨＋因子濾網'
    if reverse>trend:return '均值回歸＋因子濾網'
    return '多因子條件濾網'

def rank_items(items,names,alpha,planned_count):
    """The whole planned instrument×factor family includes failures and unfinished items."""
    ps=[];keys=[]
    for item in items:
        found={x['name']:x for x in item.get('factorStatistics',[])}
        for name in names:keys.append((item['datasetId'],name));ps.append(found.get(name,{}).get('p'))
    adjusted=dict(zip(keys,adjust_fdr(ps,'BY')))
    for item in items:
        item.update(score=None,uncappedScore=None,scoreCeiling=None,components={},recommendation='尚未完成',reasons=[],globalQ=None)
        if item['status']!='COMPLETED':
            item['recommendation']='資料檢查未通過' if item['status']=='FAILED' else '尚未完成'
            item['reasons']=[item.get('error') or '本批次尚未完成此標的。'];continue
        chosen=[x for x in item['factorStatistics'] if x['name'] in item['selected']]
        for x in item['factorStatistics']:x['scanAdjustedP']=adjusted[(item['datasetId'],x['name'])]
        q=max((x['scanAdjustedP'] for x in chosen),default=1.);item['globalQ']=q
        v=item['validation'];reasons=[]
        if item.get('validationAudit') not in [None,'PASS'] or item.get('causality') not in [None,'PASS_PREFIX']:
            item.update(score=0.,uncappedScore=0.,scoreCeiling=0.,recommendation='先修正資料／核算問題',reasons=['因果或驗證期帳戶核對未通過。']);continue
        if not chosen:reasons.append('沒有因子通過單項研究門檻。')
        if q>alpha:reasons.append('已選因子未全部通過整批 BY 校正。')
        if not chosen or q>alpha:
            item.update(score=0.,uncappedScore=0.,scoreCeiling=0.,recommendation='暫不採用因子策略',reasons=reasons);continue
        consistency=np.mean([finite(x.get('validationIC'))*x['direction']>0 for x in chosen])
        components=dict(training=30*max(0.,1-q/alpha),sharpe=20*min(1.,max(0.,finite(v.get('sharpe')))/2),recovery=20*min(1.,max(0.,finite(v.get('profitToMdd')))/2),returnQuality=15*min(1.,max(0.,finite(v.get('cagr')))/.2),icStability=15*float(consistency))
        score=round(sum(components.values()),2);uncapped=score;ceiling=100.
        if finite(v.get('netPnl'))<=0:reasons.append('驗證期成本後淨利未大於零。')
        if finite(v.get('maxDrawdown'),1)>.25:reasons.append('驗證期回撤超過 25%。')
        if finite(v.get('tradeCount'))<20:reasons.append('驗證期完成平倉腿少於 20；加減倉腿並非獨立交易樣本。')
        if v.get('sharpe') is None or finite(v.get('sharpe'))<.5:reasons.append('驗證期日頻夏普低於 0.5 或無法估計。')
        if finite(v.get('profitToMdd'))<.5:reasons.append('驗證期淨利／MDD 低於 0.5。')
        if consistency<.5:reasons.append('至少一半選入因子的驗證期 IC 未維持訓練方向。')
        readiness=item.get('readiness',{})
        if readiness.get('validationCalendarDays',0)<90 or readiness.get('validationDailyObservations',0)<60:reasons.append('驗證期不足 90 日曆日／60 個日頻觀測；不以極短樣本評為優先候選。')
        if readiness.get('doubleCostValidationNetPnl',-1)<=0:reasons.append('驗證期雙倍成本後未獲利。')
        if readiness.get('validationExcessReturn',-1)<=0:reasons.append('驗證期未超越同成本等權基準。')
        if finite(v.get('netPnl'))<=0 or finite(v.get('maxDrawdown'),1)>.25:
            recommendation='暫緩；先檢查成本與穩健性';ceiling=39.;score=min(score,ceiling)
        elif reasons:
            recommendation='延長樣本／調整成本後再驗證';ceiling=59.;score=min(score,ceiling)
        else:recommendation='可優先進行後續研究'
        plan=item.get('logicPlan') or {}
        if plan.get('status') and plan['status']!='CANDIDATE_FOR_FORWARD_TEST':
            reasons.extend(plan.get('reasons',[]));score=min(score,59.);ceiling=min(ceiling,59.);recommendation='先驗證策略假設，不直接採用'
        if not reasons:reasons=['訓練檢定與驗證期基本門檻通過；仍需獨立資料與前瞻追蹤。']
        item.update(score=score,uncappedScore=uncapped,scoreCeiling=ceiling,components={k:round(v,4) for k,v in components.items()},recommendation=recommendation,reasons=reasons)
    return dict(method='BATCH_BY',familySize=planned_count*len(names),alpha=alpha,ranking='VALIDATION_ONLY_HEURISTIC_V2',testUsedForRanking=False,weights=dict(training=30,sharpe=20,recovery=20,returnQuality=15,icStability=15),gates=dict(maxValidationDrawdown=.25,minClosedLegs=20,minSharpe=.5,minProfitToMdd=.5,minICConsistency=.5,minValidationCalendarDays=90,minValidationDailyObservations=60,requirePositiveDoubleCostValidation=True,requirePositiveValidationExcessReturn=True),limits='Heuristic research priority, not expected return, probability of profit or buy/sell advice. All displayed periods are known historical evidence. Different periods/timeframes/feeds are not directly comparable. Batch BY is an asymptotic historical diagnostic across planned datasets, not a common-time forward selection guarantee. Manual repeated scans are not adjusted.')

def scan(payload,metric_fn,clean_fn):
    s=settings(payload);datasets=payload['datasets'];names=payload.get('tools',{}).get('factors',list(NAMES))
    if not isinstance(names,list) or not names or len(set(names))!=len(names) or any(x not in NAMES for x in names):raise ValueError('請選不重複的內建因子。')
    # Freeze a stable snapshot of every requested dataset; no hardcoded symbol or currency restrictions.
    items=[dict(datasetId=d['id'],datasetName=d['name'],symbol=d['identity']['symbol'],timeframe=d['identity']['timeframe'],currency=d['identity']['currency'],source=d['identity']['source'],synthetic=bool(d['identity'].get('synthetic')),start=d['quality'].get('start'),end=d['quality'].get('end'),rows=len(d['bars']),status='PENDING') for d in datasets]
    folder=Path.cwd()/'scan-results';folder.mkdir(exist_ok=True)
    def snapshot(status):
        rule=rank_items(items,names,s['fdrAlpha'],len(datasets))
        result=dict(kind='scan',protocolVersion=VERSION,status=status,items=items,ranking=rule,config=s,factors=names,total=len(items),completed=sum(x['status']=='COMPLETED' for x in items),failed=sum(x['status']=='FAILED' for x in items),environment=dict(python=platform.python_version(),numpy=np.__version__,pandas=pd.__version__),evidence='KNOWN_EVALUATION',activation_state='INACTIVE')
        atomic_json(Path.cwd()/'scan-progress.json',clean_fn(result))
        return result
    snapshot('RUNNING')
    for i,d in enumerate(datasets):
        item=items[i];print(f'掃描 {i+1}/{len(items)} · {item["symbol"]} · {item["timeframe"]}',flush=True)
        try:
            request=dict(action='factor',datasets=[d],config=payload.get('config',{}),tools=dict(protocol='robust_v2',mode='single',factors=names,calendarPolicy='observed_bars'))
            # Keep progress bounded; the original factor job result remains complete and inspectable.
            cache=Path(payload['cacheDir']) if payload.get('cacheDir') else None
            cache_key=hashlib.sha256(json.dumps(dict(dataset=d,config=request['config'],tools=request['tools'],program=payload.get('cacheNamespace'),environment=[platform.python_version(),np.__version__,pd.__version__]),sort_keys=True,separators=(',',':')).encode()).hexdigest()
            cached=cache/(cache_key+'.json') if cache else None;r=None
            if cached and payload.get('cacheNamespace'):
                try:r=json.loads(cached.read_text(encoding='utf-8'));print('  快取命中 · 相同資料、參數、程式版本與環境',flush=True)
                except (OSError,ValueError):r=None
            if r is None:
                with contextlib.redirect_stdout(io.StringIO()):r=clean_fn(research(request,metric_fn))
                if cached and payload.get('cacheNamespace'):
                    cache.mkdir(parents=True,exist_ok=True);atomic_json(cached,r)
            file=f'scan-results/result-{i}.json';Path(file).write_text(json.dumps(clean_fn(r),ensure_ascii=False,allow_nan=False,separators=(',',':')),encoding='utf-8')
            compact=lambda key:{k:r[key]['metrics'].get(k) for k in METRICS}
            item['comparisonGroup']=' | '.join([item['timeframe'],item['currency'],r['protocol']['splitDates']['validationStart'],r['protocol']['splitDates']['validationEnd']])
            item['sourceQuality']=r['quality']['sourceQuality'];style=strategy_style(r['factors']);next_step='可比較均線／Donchian 趨勢範本，檢查突破後回撤與持有期。' if style.startswith('趨勢') else '可比較 RSI／布林均值回歸範本，優先檢查換手與雙倍成本。' if style.startswith('均值') else '可比較加入濾網前後的成本後績效，檢驗增量效益。'
            item.update(status='COMPLETED',recordFile=file,selected=r['selected'],strategyStyle=style,researchNextStep=(r['logicPlan']['entry']+' '+r['logicPlan']['exit']) if r.get('logicPlan') else next_step,readiness=r.get('readiness'),logicPlan=r.get('logicPlan'),validationAudit=r['validation']['executionAudit']['status'],causality=r['causality']['status'],validation=compact('validation'),evaluation=compact('evaluation'),gross=compact('gross'),costStress=compact('costStress'),baseline=compact('baseline'),splitDates=r['protocol']['splitDates'],factorStatistics=[dict(name=x['name'],direction=x['direction'],selectionStatus=x['selectionStatus'],p=x['development']['inference']['p'],localAdjustedP=x['adjustedP'],trainingIC=x['development']['ic'],validationIC=x['validation']['ic']) for x in r['factors']])
        except Exception as error:item.update(status='FAILED',error=str(error)[:1000])
        snapshot('RUNNING')
    return clean_fn(snapshot('COMPLETED' if all(x['status']=='COMPLETED' for x in items) else 'COMPLETED_WITH_EXCLUSIONS'))
