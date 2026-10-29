"""Independent accounts, common source and parameters; never a pooled portfolio."""
import json,copy,contextlib,io
from pathlib import Path
from atomic_file import atomic_json

def run_batch(payload,runner,clean):
    items=[dict(datasetId=d['id'],name=d['name'],symbol=d['identity']['symbol'],currency=d['identity']['currency'],timeframe=d['identity']['timeframe'],status='PENDING') for d in payload['datasets']]
    Path('batch-results').mkdir(exist_ok=True)
    def snapshot(status):
        value=dict(kind='batch',status=status,items=items,total=len(items),completed=sum(x['status']=='COMPLETED' for x in items),failed=sum(x['status']=='FAILED' for x in items),scope='INDEPENDENT_ACCOUNTS_NOT_PORTFOLIO',selection='Each dataset uses its own preceding IS only; no cross-asset OOS winner selection.')
        atomic_json(Path('batch-progress.json'),clean(value));return value
    snapshot('RUNNING')
    for i,d in enumerate(payload['datasets']):
        print(f'進度 {int(i/len(items)*90)+5}% · 批次 {i+1}/{len(items)} · {d["identity"]["symbol"]}',flush=True)
        try:
            request={k:copy.deepcopy(v) for k,v in payload.items() if k!='datasets'};request.update(action='research',dataset=d)
            if payload.get('tools',{}).get('nativePeriods',True):request['config'].pop('timeframeMinutes',None)
            with contextlib.redirect_stdout(io.StringIO()):result=runner(request)
            file=f'batch-results/result-{i}.json';Path(file).write_text(json.dumps(clean(result),ensure_ascii=False,allow_nan=False,separators=(',',':')),encoding='utf-8')
            items[i].update(status='COMPLETED',recordFile=file,metrics=result['evaluation']['metrics'],parameters=result['parameters'],config=result['config'])
        except Exception as e:items[i].update(status='FAILED',error=f'{type(e).__name__}: {e}'[:1400])
        snapshot('RUNNING')
    return snapshot('COMPLETED' if all(x['status']=='COMPLETED' for x in items) else 'COMPLETED_WITH_EXCLUSIONS')
