"""Seeded conditional daily return resampling. No order-fill or parameter-search simulation."""
import math
import numpy as np

def simulate(nav,capital,settings):
    r=np.asarray([x['return'] for x in nav],float);n=len(r);count=int(settings.get('simulations',300));block=int(settings.get('blockSize',10));seed=int(settings.get('seed',42));method=settings.get('method','stationary')
    floor=float(settings.get('equityFloor',.7))
    if n<2 or not np.isfinite(r).all() or np.any(r<=-1):raise ValueError('MC 需要至少兩個有限日報酬，且每期報酬須大於 -100%。')
    if not 100<=count<=2000 or not 1<=block<=min(250,n) or method not in ['stationary','circular_block','iid','shuffle'] or not 0<floor<1 or not 0<=seed<=2147483647:raise ValueError('MC 次數 100–2000、區塊 1–250 且不超過樣本；請使用合法種子與資金底線。')
    times=np.unique(np.linspace(0,n,min(161,n+1)).astype(int))
    def sample(length,reps,local_seed,details):
        rng=np.random.default_rng(local_seed);ends=[];dds=[];amounts=[];durations=[];sh=[];breached=[];sampled=[];paths=[]
        # Bound working memory even for long intraday histories aggregated to daily observations.
        for start in range(0,reps,64):
            m=min(64,reps-start)
            if method=='shuffle':v=np.array([rng.permutation(r) for _ in range(m)])
            elif method=='iid':v=r[rng.integers(0,n,size=(m,n))]
            elif method=='circular_block':
                idx=(rng.integers(0,n,size=(m,math.ceil(n/length),1))+np.arange(length)).reshape(m,-1)[:,:n]%n;v=r[idx]
            else:
                idx=np.empty((m,n),int);idx[:,0]=rng.integers(0,n,size=m)
                for j in range(1,n):
                    restart=rng.random(m)<1/length;idx[:,j]=np.where(restart,rng.integers(0,n,size=m),(idx[:,j-1]+1)%n)
                v=r[idx]
            curves=np.column_stack([np.full(m,capital),capital*np.cumprod(1+v,axis=1)]);peaks=np.maximum.accumulate(curves,axis=1)
            ends.extend(curves[:,-1]/capital-1);dds.extend(np.max(1-curves/peaks,axis=1));amounts.extend(np.max(peaks-curves,axis=1));breached.extend(np.min(curves,axis=1)<=capital*floor)
            sd=v.std(axis=1,ddof=1);sh.extend(np.divide(v.mean(axis=1)*np.sqrt(365),sd,out=np.full(m,np.nan),where=sd>1e-15))
            duration=np.zeros(m,int);longest=duration.copy()
            for j in range(1,n+1):duration=np.where(curves[:,j]<peaks[:,j]-1e-8,duration+1,0);longest=np.maximum(longest,duration)
            durations.extend(longest)
            if details:
                sampled.extend(curves[:,times]);paths.extend(curves[:max(0,min(12-len(paths),m)),times].tolist())
        return dict(ends=np.array(ends),dd=np.array(dds),amount=np.array(amounts),duration=np.array(durations),sh=np.array(sh),breach=np.array(breached),sampled=np.array(sampled),paths=paths)
    data=sample(block,count,seed,True)
    def qs(a):
        a=np.asarray(a);a=a[np.isfinite(a)];return {f'p{int(q*100):02d}':float(np.quantile(a,q)) if len(a) else None for q in [.05,.5,.95]}
    sensitivity=[]
    if method in ['stationary','circular_block']:
        for length in sorted(set([max(1,block//2),block,min(n,250,block*2)])):
            v=data if length==block else sample(length,min(count,200),seed+length,False)
            sensitivity.append(dict(blockSize=length,simulations=count if length==block else min(count,200),terminalReturn=qs(v['ends']),maxDrawdownAmount=qs(v['amount']),floorBreachFraction=float(v['breach'].mean())))
    historical=np.r_[capital,capital*np.cumprod(1+r)];peak=np.maximum.accumulate(historical)
    def hist(a):
        counts,edges=np.histogram(a,bins=24);return [dict(low=float(edges[i]),high=float(edges[i+1]),count=int(c)) for i,c in enumerate(counts)]
    return dict(protocolVersion='CONDITIONAL_MC_V2',method=method,seed=seed,simulations=count,blockSize=block,observations=n,annualization=365,equityFloor=floor,floorBreachFraction=float(data['breach'].mean()),gainFraction=float(np.mean(data['ends']>0)),terminalReturn=qs(data['ends']),maxDrawdown=qs(data['dd']),maxDrawdownAmount=qs(data['amount']),maxUnderwaterDays=qs(data['duration']),sharpe=qs(data['sh']),historical=dict(terminalReturn=float(historical[-1]/capital-1),maxDrawdownAmount=float(np.max(peak-historical))),fan=[dict(bar=int(t),**{f'p{int(q*100):02d}':float(np.quantile(data['sampled'][:,j],q)) for q in [.05,.5,.95]}) for j,t in enumerate(times)],paths=[dict(name=f'路徑 {i+1}',values=v) for i,v in enumerate(data['paths'])],distribution=[dict(**{'return':float(a)},maxDrawdown=float(b),maxDrawdownAmount=float(c)) for a,b,c in zip(data['ends'],data['dd'],data['amount'])],histograms=dict(terminalReturn=hist(data['ends']),maxDrawdownAmount=hist(data['amount'])),sensitivity=sensitivity,scope='固定策略 OOS 成本後日報酬的條件重抽樣；Stationary 使用幾何分布區塊長度，Circular 使用固定區塊。未重新模擬馬丁加倉、保證金、破產平倉、流動性或選參；資金底線觸及比例不是券商爆倉機率，也不是未來獲利機率。分位帶是逐點分位數，不是同一路徑或同時信賴帶。')
