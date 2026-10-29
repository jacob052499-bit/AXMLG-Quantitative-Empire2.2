"""Transparent inference for the built-in factor family; asymptotic, not discovery proof."""
import math
import numpy as np
import pandas as pd

def block_rank_ic(a,b,size):
    """Pairwise missing values and average ranks; vectorized non-overlapping blocks."""
    n=len(a);rows=math.ceil(n/size);aa=np.full(rows*size,np.nan);bb=aa.copy();aa[:n]=a;bb[:n]=b
    aa=aa.reshape(rows,size);bb=bb.reshape(rows,size);valid=np.isfinite(aa)&np.isfinite(bb);counts=valid.sum(axis=1)
    ar=pd.DataFrame(np.where(valid,aa,np.nan)).rank(axis=1).to_numpy();br=pd.DataFrame(np.where(valid,bb,np.nan)).rank(axis=1).to_numpy()
    am=np.nansum(ar,axis=1)/np.maximum(counts,1);bm=np.nansum(br,axis=1)/np.maximum(counts,1)
    ac=ar-am[:,None];bc=br-bm[:,None];den=np.sqrt(np.nansum(ac*ac,axis=1)*np.nansum(bc*bc,axis=1))
    out=np.divide(np.nansum(ac*bc,axis=1),den,out=np.full(rows,np.nan),where=den>0);out[counts<max(20,size//2)]=np.nan
    return out,counts

def hac_mean(values, lags=None, minimum=20):
    a=np.asarray(values,dtype=float);a=a[np.isfinite(a)];n=len(a)
    out=dict(n=n,mean=float(a.mean()) if n else None,se=None,t=None,p=None,ci95=None,lags=0,status='INSUFFICIENT_OBSERVATIONS')
    if n<minimum:return out
    lag=min(n-1,int(lags) if lags is not None else max(1,int(4*(n/100)**(2/9))))
    z=a-a.mean();lrv=float(z@z/n)
    for k in range(1,lag+1):lrv+=2*(1-k/(lag+1))*float(z[k:]@z[:-k]/n)
    out['lags']=lag
    if lrv<=1e-16:out['status']='DEGENERATE_VARIANCE';return out
    se=math.sqrt(lrv/n);t=float(a.mean()/se)
    out.update(se=se,t=t,p=math.erfc(abs(t)/math.sqrt(2)),ci95=[max(-1.,float(a.mean()-1.96*se)),min(1.,float(a.mean()+1.96*se))],status='ASYMPTOTIC_HAC')
    return out

def adjust_fdr(p_values, method='BY'):
    if method not in ('BY','BH','none'):raise ValueError('FDR 方法必須為 BY、BH 或 none。')
    p=np.array([1. if x is None or not np.isfinite(x) else float(x) for x in p_values]);m=len(p)
    if np.any((p<0)|(p>1)):raise ValueError('p 值超出 [0,1]。')
    if method=='none' or not m:return p.tolist()
    order=np.argsort(p,kind='stable');mult=sum(1/k for k in range(1,m+1)) if method=='BY' else 1
    adjusted=np.minimum.accumulate((p[order]*m*mult/np.arange(1,m+1))[::-1])[::-1]
    out=np.empty(m);out[order]=np.minimum(1,adjusted);return out.tolist()

def ecdf_score(train, values, winsor=.01):
    a=np.asarray(train,dtype=float);a=np.sort(a[np.isfinite(a)])
    if not len(a):return np.full(len(values),np.nan)
    lo,hi=np.quantile(a,[winsor,1-winsor]);a=np.clip(a,lo,hi);x=np.asarray(values,dtype=float);clipped=np.clip(x,lo,hi) if winsor else x
    out=(np.searchsorted(a,clipped,side='left')+np.searchsorted(a,clipped,side='right'))/(2*len(a));out[~np.isfinite(x)]=np.nan
    return out

def segment_stats(x, y, mode, rank_ic, start, end, horizon, edges=None, min_assets=5, block_size=60):
    """Block IC for a time series; per-date cross-sectional IC for a universe."""
    xx=x.iloc[start:end];yy=y.iloc[start:end];series=[]
    if mode=='single':
        a=xx.iloc[:,0];b=yy.iloc[:,0];mask=a.notna()&b.notna();coverage=float(mask.mean()) if len(mask) else 0
        ic=rank_ic(a,b);edges=np.unique(a[mask].quantile([.2,.4,.6,.8]).to_numpy()) if edges is None and mask.any() else np.array([]) if edges is None else edges
        bins=np.searchsorted(edges,a[mask].to_numpy(),side='right');vals=b[mask].to_numpy()
        groups=[dict(group=j+1,n=int((bins==j).sum()),meanReturn=float(vals[bins==j].mean()) if (bins==j).any() else None) for j in range(len(edges)+1)]
        correlations,counts=block_rank_ic(a.to_numpy(),b.to_numpy(),block_size)
        series=[dict(date=a.index[min((j+1)*block_size,len(a))-1].isoformat(),value=float(v),n=int(counts[j])) for j,v in enumerate(correlations) if np.isfinite(v)]
        inference=hac_mean([r['value'] for r in series]);sample_n=int(mask.sum())
    else:
        mask=xx.notna()&yy.notna();xr=xx.where(mask).rank(axis=1,pct=True);yr=yy.where(mask).rank(axis=1,pct=True)
        xc=xr.sub(xr.mean(axis=1),axis=0);yc=yr.sub(yr.mean(axis=1),axis=0)
        corr=(xc*yc).sum(axis=1)/np.sqrt(xc.pow(2).sum(axis=1)*yc.pow(2).sum(axis=1));counts=mask.sum(axis=1)
        corr=corr.where(counts>=min_assets).replace([np.inf,-np.inf],np.nan);series=[dict(date=t.isoformat(),value=float(v),n=int(counts.loc[t])) for t,v in corr.dropna().items()]
        ic=float(corr.mean()) if corr.notna().any() else None;coverage=float(corr.notna().mean()) if len(corr) else 0;sample_n=len(series)
        groups=[]
        for j in range(5):
            member=(np.clip(np.ceil(xr*5)-1,0,4)==j)&mask&corr.notna().to_numpy()[:,None];v=yy.where(member).stack().dropna()
            groups.append(dict(group=j+1,n=len(v),meanReturn=float(v.mean()) if len(v) else None))
        inference=hac_mean([r['value'] for r in series],lags=max(horizon,int(4*(max(1,len(series))/100)**(2/9))))
    values=np.array([r['value'] for r in series]);sd=float(values.std(ddof=1)) if len(values)>1 else 0
    spread=groups[-1]['meanReturn']-groups[0]['meanReturn'] if len(groups)>1 and groups[-1]['meanReturn'] is not None and groups[0]['meanReturn'] is not None else None
    return dict(n=sample_n,coverage=coverage,ic=ic,groups=groups,spread=spread,blockIC=values.tolist(),blockICIR=float(values.mean()/sd) if sd>1e-12 else None,icSeries=series,inference=inference),edges
