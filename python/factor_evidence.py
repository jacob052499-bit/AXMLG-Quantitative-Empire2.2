"""Evidence-to-Logic EL1: disclosed research rules, not a predictive probability model."""
import inspect,math
import numpy as np

def evidence(values,direction,seed=42):
    a=np.asarray(values,float);a=a[np.isfinite(a)];n=len(a)
    if n<20:return dict(status='INSUFFICIENT_BLOCKS',n=n,ci95=None,folds=[],forwardSignConsistency=None)
    block=max(1,min(5,int(n**(1/3))));rng=np.random.default_rng(seed);means=[]
    for start in range(0,499,64):
        m=min(64,499-start);idx=(rng.integers(0,n,size=(m,math.ceil(n/block),1))+np.arange(block)).reshape(m,-1)[:,:n]%n;means.extend(a[idx].mean(axis=1))
    folds=[];edges=np.linspace(0,n,5).astype(int)
    for j in range(1,4):
        train=a[:edges[j]];test=a[edges[j]:edges[j+1]];d=1 if train.mean()>=0 else -1
        folds.append(dict(trainBlocks=len(train),validationBlocks=len(test),trainingMeanIC=float(train.mean()),forwardMeanIC=float(test.mean()),pastDirection=d,positive=bool(test.mean()*d>0)))
    return dict(status='APPROXIMATE_BLOCK_BOOTSTRAP',n=n,ci95=np.quantile(means,[.025,.975]).tolist(),blockLength=block,resamples=499,seed=seed,folds=folds,forwardSignConsistency=sum(x['positive'] for x in folds)/len(folds),scope='IC 區塊的 circular bootstrap 及三個前序訓練窗口；區間為近似逐因子區間，非同時信賴區間。窗口內樣本少，不作精確顯著性保證。')

def weights(rows,selected,correlation):
    raw={}
    for row in rows:
        if row['name'] not in selected:continue
        values=np.asarray(row['development']['blockIC']);n=len(values);sd=values.std(ddof=1) if n>1 else 0
        stability=row.get('robustEvidence',{}).get('forwardSignConsistency') or 0
        redundancy=1+sum(abs(float(correlation.loc[row['name'],k])) for k in selected if k!=row['name'] and np.isfinite(correlation.loc[row['name'],k]))
        raw[row['name']]=min(2,abs(float(values.mean()))/(sd+.05))*n/(n+40)*stability/redundancy
    total=sum(raw.values());return {k:(v/total if total>1e-15 else 1/len(selected)) for k,v in raw.items()}

def equal_mdd_excess(a,b,capital):
    def pnl(section):return np.array([x['nav']-capital for x in section['nav']],float)
    x,y=pnl(a),pnl(b)
    if len(x)!=len(y) or any(u['date']!=v['date'] for u,v in zip(a['nav'],b['nav'])):return None
    dd=lambda z:float(np.max(np.maximum.accumulate(np.r_[0.,z])[1:]-z))
    dx,dy=dd(x),dd(y)
    if min(dx,dy)<=1e-10:return None
    target=min(dx,dy);return dict(strategyMdd=dx,benchmarkMdd=dy,targetMdd=target,strategyMultiplier=target/dx,benchmarkMultiplier=target/dy,excessPnl=float(x[-1]*target/dx-y[-1]*target/dy),basis='VALIDATION_EXACT_BAR_CLOSE_AFFINE_PNL')

def logic_plan(rows,selected,weight,s,validation,stress,baseline,readiness,freeze,mode,identity):
    matched=equal_mdd_excess(validation,baseline,s['capital']);reasons=[]
    if not selected:reasons.append('沒有因子通過訓練期多重檢定、方向及穩定性門檻。')
    if readiness['validationCalendarDays']<90 or readiness['validationDailyObservations']<60:reasons.append('驗證期不足 90 日曆日與 60 日觀測。')
    if validation['metrics']['netPnl']<=0:reasons.append('驗證期成本後虧損。')
    if stress['metrics']['netPnl']<=0:reasons.append('驗證期雙倍成本未獲利。')
    if matched is None:reasons.append('基準或策略沒有可估計回撤，等 MDD 超額收益不可用。')
    elif matched['excessPnl']<=0:reasons.append('驗證期在相同 MDD 下未超越基準。')
    status='HOLD_CASH' if not selected else 'RESEARCH_HYPOTHESIS_ONLY' if reasons else 'CANDIDATE_FOR_FORWARD_TEST'
    components=[dict(name=r['name'],label=r['label'],direction=r['direction'],weight=weight.get(r['name'],0),trainingIC=r['development']['ic'],adjustedP=r['adjustedP'],bootstrapCI=r.get('robustEvidence',{}).get('ci95'),forwardSignConsistency=r.get('robustEvidence',{}).get('forwardSignConsistency'),role='高排名加分' if r['direction']>0 else '低排名加分') for r in rows if r['name'] in selected]
    return dict(version='EVIDENCE_TO_LOGIC_EL1',status=status,reasons=reasons or ['訓練與驗證門檻通過；下一步仍需獨立資料與前瞻觀察。'],components=components,weights=weight,freezeTime=freeze,mode=mode,entry=f'因子方向只看訓練期；加權排名分數 ≥ {s["scoreThreshold"]:.2f} 後，下一根可觀察開盤進場。' if mode=='single' else f'每 {s["rebalanceBars"]} 根依前一完成 K 線分數選前 {s["topAssets"]} 個商品，遵守配置與集中上限。',exit=f'分數 < {s["exitThreshold"]:.2f} 退出；進出門檻分離以減少反覆換手。' if mode=='single' else f'保留排名緩衝 {s["rankBuffer"]} 名；目標權重變化小於 {s["rebalanceBand"]:.1%} 時不交易。',sizing=f'價格報酬長倉／現金模型，配置上限 {s["allocation"]:.0%}；並非 CFD 標準手或券商保證金。',invalidation='成本後驗證虧損、區塊方向不穩、相同 MDD 超額收益不正或前瞻追蹤失效時，暫停採用。',matchedValidation=matched,weightFormula='訓練 IC 訊噪比（上限2） × n/(n+40) × 前序窗口同向比例 ÷ (1+與選入因子絕對相關總和)，再正規化。所有常數為本 App 事前研究規則，並非文獻保證的最佳值。',nextExperiment='固定本輪方向與權重，記錄下一段未觀察資料；比較成本與換手。不得看最終測試後回頭替換因子。',limits='EL1 是透明研究流程，未經外部校準；分數不是上漲機率，不宣稱最準確或保證 Alpha。未提供 PIT 財報與公司行動資料時，不冒充價值、品質或基本面因子。',sourceIds=['HLZ2016','NW1987','BY2001','NV2016','AHM2019','HXZ2020'])

def python_draft(plan,s,identity):
    if plan['mode']!='single' or not plan['components']:return None
    from factors import features
    from factor_statistics import ecdf_score
    import json
    definitions=inspect.getsource(features)+'\n'+inspect.getsource(ecdf_score)
    fixed=dict(components=plan['components'],windows={k:s[k] for k in ['shortWindow','mediumWindow','longWindow']},winsor=s['winsorQuantile'],entry=s['scoreThreshold'],exit=s['exitThreshold'],freeze=plan['freezeTime'],fitEnd=plan['trainingFeatureEnd'])
    source='# EL1 因子研究草稿；與因子投資的逐根再平衡帳本不同，需重新回測。\n# 凍結方向／權重；原始訓練結束前不交易，禁止將之當作早期可得資訊。\nFROZEN = '+repr(fixed)+'\n\n'+definitions+'''
def fit(train, params):
    boundary = pd.Timestamp(FROZEN['freeze'])
    if train.index[-1] < boundary:
        raise ValueError('訓練資料尚未涵蓋原研究凍結時間，拒絕前視使用因子方向與權重。')
    f = features(train.loc[train.index <= pd.Timestamp(FROZEN['fitEnd'])], FROZEN['windows'])
    return {c['name']: np.sort(f[c['name']].dropna().to_numpy()) for c in FROZEN['components']}

def strategy(df, params, model):
    f = features(df, FROZEN['windows'])
    score = np.zeros(len(df))
    for c in FROZEN['components']:
        value = ecdf_score(model[c['name']], f[c['name']], FROZEN['winsor'])
        score += c['weight'] * (value if c['direction'] > 0 else 1-value)
    signal = np.zeros(len(df), dtype=int)
    holding = False
    for i, value in enumerate(score):
        if df.index[i] <= pd.Timestamp(FROZEN['freeze']) or not np.isfinite(value):
            holding = False
        elif value >= FROZEN['entry']:
            holding = True
        elif value < FROZEN['exit']:
            holding = False
        signal[i] = int(holding)
    return pd.Series(signal, index=df.index)
'''
    return dict(source=source,params={},form=dict(executionMode='legacy',capital=s['capital'],commissionBps=s['commissionBps'],slippageBps=s['slippageBps'],spreadBps=s['spreadBps'],allocation=s['allocation']*100,split=s['trainFraction']*100,gapBars=s['embargoBars'],gridEnabled=False,mcEnabled=True,lotSize=s['lotSize'],timeframeMinutes=int(s['barHours']*60)),scope='固定訓練方向／權重的訊號草稿，僅於原訓練結束後產生訊號。工作台持有期間不逐根再平衡；重新回測結果不能冒充原投資帳本。')
