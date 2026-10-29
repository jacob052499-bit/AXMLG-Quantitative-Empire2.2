"""Strict UTC-epoch OHLCV aggregation. Never invent finer bars or bridge gaps."""
import re
import pandas as pd

PERIODS=[1,5,15,30,60,90,120,240,1440]

def minutes(value):
    if value=='1 Day':return 1440
    if value=='1 Hour':return 60
    match=re.fullmatch(r'(\d+) Min',str(value))
    if match and int(match[1]) in PERIODS:return int(match[1])
    raise ValueError('不支援的行情週期。')

def prepare(dataset,config):
    source=minutes(dataset['identity']['timeframe']);raw=config.get('timeframeMinutes',source)
    if type(raw) not in [int,float] or int(raw)!=raw or int(raw) not in PERIODS:raise ValueError('K 線週期須為 1/5/15/30/60/90/120/240/1440 分鐘。')
    target=int(raw)
    if target<source or target%source:raise ValueError(f'來源為 {source}M，不能建立 {target}M；需要更細且能整除的原始行情（建議 1M）。')
    frame=pd.DataFrame(dataset['bars']);frame.index=pd.to_datetime(frame.pop('time'),unit='ms',utc=True);frame.index.name='timestamp'
    source_rows=len(frame);dropped=0
    if target!=source:
        group=frame.resample(f'{target}min',origin='epoch',closed='left',label='left')
        counts=group.close.count();aggregated=group.agg(dict(open='first',high='max',low='min',close='last',volume='sum'))
        expected=target//source;valid=counts.eq(expected)
        dropped=int(((counts>0)&~valid).sum());frame=aggregated.loc[valid].dropna()
    for key in ['startDate','endDate']:
        value=config.get(key,'')
        if value:
            if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError('研究日期須為 YYYY-MM-DD。')
            boundary=pd.Timestamp(value,tz='UTC')
            frame=frame.loc[frame.index>=boundary] if key=='startDate' else frame.loc[frame.index+pd.Timedelta(minutes=target)<=boundary+pd.Timedelta(days=1)]
    if len(frame)<120:raise ValueError('所選週期／期間在排除不完整 K 線後不足 120 根。')
    info=dict(sourceMinutes=source,timeframeMinutes=target,sourceRows=source_rows,rows=len(frame),droppedIncompleteBuckets=dropped,start=frame.index[0].isoformat(),end=frame.index[-1].isoformat(),alignment='UTC epoch; left-closed, bar-open labels; exact complete source counts; gaps and partial buckets dropped, no fabricated prices',dateRange='UTC dates; end includes only bars completed by next midnight')
    return frame,info
