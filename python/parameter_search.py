"""Bounded exhaustive IS search, deterministic batches and durable resumable checkpoints."""
import concurrent.futures as cf
import contextlib,hashlib,io,itertools,json,math,os,time
from pathlib import Path
import numpy as np
import pandas as pd

MAX_CANDIDATES=100000
MAX_AXIS=1000
METRICS=['netPnl','sharpe','maxDrawdown','maxDrawdownAmount','reportMaxDrawdownAmount','reportNetPnl','profitToMdd','tradeCount','winRate','profitFactor','cagr','observations','dailyMean','dailyStd']
_context=None

def initialize(code,frame,config,start,end,compact):
    global _context
    _context=(code,frame,config,start,end,compact)

def evaluate_batch(batch):
    import worker
    code,frame,c,start,end,compact=_context;out=[]
    for index,params in batch:
        trial=dict(trialId=f'candidate-{index+1:03d}',params=params,evidence='DEVELOPMENT',eligible=False,metrics=None,status='FAILED')
        try:
            np.random.seed(42)
            with contextlib.redirect_stdout(io.StringIO()):
                model=worker.fit_model(code,frame.iloc[start:end],params)
                signals=worker.signals_for(code,frame.iloc[:end],params,model)
                result=worker.backtest(frame.iloc[:end],signals,c,start,end,record=False)
            m=result['metrics'];reasons=[]
            if m['tradeCount']<c['minTrades']:reasons.append('完成交易不足')
            if m['sharpe'] is None:reasons.append('Sharpe 無法估計')
            if m['netPnl']<=0:reasons.append('IS 成本後未獲利')
            if m['maxDrawdown']>c['maxDrawdown']:reasons.append('IS 回撤超過設定門檻')
            trial.update(metrics={k:m.get(k) for k in METRICS} if compact else m,eligible=not reasons,reasons=reasons,status='COMPLETED')
        except Exception as e:trial['error']=f'{type(e).__name__}: {str(e)[:700]}'
        out.append(worker.clean(trial))
    return out

def atomic(path,value):
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')),encoding='utf-8')
    for attempt in range(8):
        try:temp.replace(path);return
        except PermissionError:
            if attempt==7:raise
            time.sleep(.03*(attempt+1))

def run(code,frame,c,params,grid,start,end,workers=2,checkpoint=True):
    import worker
    candidates=worker.candidate_list(params,grid);total=len(candidates);compact=total>225
    # Hash the actual input frame and contract, not only endpoints or a user supplied path.
    digest=hashlib.sha256(pd.util.hash_pandas_object(frame,index=True).values.tobytes()+json.dumps(dict(code=code,c=c,params=params,grid=grid,start=start,end=end,version=1),sort_keys=True).encode()).hexdigest()
    prefix=f'search-{start}-{end}';meta=Path.cwd()/(prefix+'.meta.json');ledger=Path.cwd()/(prefix+'.ndjson');trials={};began=time.monotonic();resumed=0
    if checkpoint and meta.exists():
        if json.loads(meta.read_text(encoding='utf-8'))['signature']!=digest:raise ValueError('選參續跑契約不符，拒絕混入其他資料或設定。')
        lines=ledger.read_text(encoding='utf-8').splitlines() if ledger.exists() else []
        for j,line in enumerate(lines):
            try:t=json.loads(line)
            except ValueError:
                if j==len(lines)-1:break
                raise ValueError('選參 checkpoint 中段損壞。')
            index=int(t['trialId'].split('-')[1])-1
            if not 0<=index<total or t['params']!=candidates[index]:raise ValueError('選參 checkpoint 參數不符。')
            trials[index]=t
        resumed=len(trials)
        # Rewrite once so an interrupted trailing write cannot corrupt the next appended record.
        ledger.write_text(''.join(json.dumps(t,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n' for _,t in sorted(trials.items())),encoding='utf-8')
    elif checkpoint:
        atomic(meta,dict(signature=digest,total=total,version=1));ledger.write_text('',encoding='utf-8')
    workers=max(1,min(4,int(workers),max(1,(os.cpu_count() or 2)-1))) if compact else 1
    def progress(status='RUNNING'):
        done=len(trials);elapsed=time.monotonic()-began;new=done-resumed;eta=(total-done)*elapsed/new if new else None
        p=dict(status=status,total=total,completed=done,failed=sum(t['status']=='FAILED' for t in trials.values()),eligible=sum(t['eligible'] for t in trials.values()),resumed=resumed,workers=workers,elapsedSeconds=elapsed,remainingSeconds=eta,marketBars=end-start,checkpoint=prefix,signature=digest,scope='IS_ONLY_EXHAUSTIVE_GRID')
        if checkpoint:atomic(Path.cwd()/'search-progress.json',p)
        print(f'進度 {15+int(18*done/total)}% · IS 參數 {done}/{total}，{workers} 個程序，剩餘估計 '+(f'{eta:.0f} 秒' if eta is not None else '計算中'),flush=True)
        return p
    def save(rows):
        if checkpoint:
            with ledger.open('a',encoding='utf-8') as f:
                for t in rows:f.write(json.dumps(t,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n')
                f.flush();os.fsync(f.fileno())
        for t in rows:trials[int(t['trialId'].split('-')[1])-1]=t
        progress()
    pending=((i,p) for i,p in enumerate(candidates) if i not in trials)
    def batches():
        while True:
            b=list(itertools.islice(pending,16 if compact else 1))
            if not b:return
            yield b
    progress()
    if workers==1:
        initialize(code,frame,c,start,end,compact)
        for batch in batches():save(evaluate_batch(batch))
    else:
        with cf.ProcessPoolExecutor(max_workers=workers,initializer=initialize,initargs=(code,frame,c,start,end,compact)) as pool:
            iterator=iter(batches());active=set()
            for _ in range(workers*2):
                batch=next(iterator,None)
                if batch:active.add(pool.submit(evaluate_batch,batch))
            while active:
                ready,active=cf.wait(active,return_when=cf.FIRST_COMPLETED)
                for task in ready:
                    save(task.result());batch=next(iterator,None)
                    if batch:active.add(pool.submit(evaluate_batch,batch))
    summary=progress('COMPLETED');return [trials[i] for i in range(total)],summary

def selection_bias(trials,selected_returns,selected_sharpe):
    """Bailey–Lopez de Prado normal approximation; N assumes independent trials, disclosed."""
    from statistics import NormalDist
    r=np.asarray(selected_returns,float);r=r[np.isfinite(r)];srs=np.array([t['metrics']['sharpe']/math.sqrt(365) for t in trials if t.get('metrics') and t['metrics'].get('sharpe') is not None]);n=len(r);N=len(trials)
    out=dict(method='DSR_APPROX_INDEPENDENT_TRIAL_COUNT',trials=N,finiteTrials=len(srs),observations=n,status='UNDEFINED',scope='漸近選擇偏誤診斷；候選高度相關，獨立試驗數未知。本次採全部嘗試數作情境，未涵蓋跨研究試參，不是獲利機率或完整 PBO。')
    if n<20 or len(srs)<2 or selected_sharpe is None or r.std(ddof=1)<1e-15:return out
    z=(r-r.mean())/r.std(ddof=0);skew=float(np.mean(z**3));kurt=float(np.mean(z**4));sr=selected_sharpe/math.sqrt(365);normal=NormalDist();gamma=.5772156649015329
    hurdle=float(srs.std(ddof=1))*((1-gamma)*normal.inv_cdf(1-1/N)+gamma*normal.inv_cdf(1-1/(N*math.e)))
    variance=1-skew*sr+(kurt-1)*sr*sr/4
    if variance<=0:return out
    return {**out,'status':'APPROXIMATE','diagnosticProbability':normal.cdf((sr-hurdle)*math.sqrt(n-1)/math.sqrt(variance)),'annualizedHurdle':hurdle*math.sqrt(365),'skewness':skew,'kurtosis':kurt,'independenceAssumption':True}
