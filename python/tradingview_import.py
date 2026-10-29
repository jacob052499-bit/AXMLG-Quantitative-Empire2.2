"""Normalize TradingView paired trade CSV/XLSX exports. No evaluation of file content."""
import sys,json,csv,io,base64,zipfile,xml.etree.ElementTree as ET,re,datetime as dt,math
from zoneinfo import ZoneInfo

def xlsx_rows(data):
    if len(data)>12*1024*1024:raise ValueError('XLSX 最大 12 MB。')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist())>60*1024*1024:raise ValueError('XLSX 解壓內容超過 60 MB。')
        shared=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            xml=ET.fromstring(z.read('xl/sharedStrings.xml'));shared=[''.join(x.itertext()) for x in xml]
        sheets=[]
        for name in z.namelist():
            if not re.fullmatch(r'xl/worksheets/sheet\d+\.xml',name):continue
            out=[]
            for row in ET.fromstring(z.read(name)).findall('.//{*}row'):
                values=[]
                for cell in row:
                    ref=cell.attrib.get('r','A1');col=0
                    for char in re.match('[A-Z]+',ref)[0]:col=col*26+ord(char)-64
                    while len(values)<col:values.append('')
                    value=cell.find('{*}v');text=value.text if value is not None else ''.join(cell.find('{*}is').itertext()) if cell.find('{*}is') is not None else ''
                    if cell.attrib.get('t')=='s':text=shared[int(text)]
                    values[col-1]=text or ''
                out.append(values)
            if out:sheets.append(out)
        for rows in sheets:
            if any(any(str(c).strip().lower() in ('trade #','trade number','交易 #','交易編號','交易编号') for c in row) for row in rows[:15]):return rows
        if len(sheets)==1:return sheets[0]
        raise ValueError('找不到交易清單工作表；請匯出 List of trades／交易清單。')

def number(value):
    s=str(value).strip().replace('\u2212','-').replace('\u00a0','').replace('%','')
    s=re.sub(r'[^0-9.eE+,\-()]','',s)
    if s.startswith('(') and s.endswith(')'):s='-'+s[1:-1]
    if ',' in s and not re.fullmatch(r'[+\-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?:[eE][+\-]?\d+)?',s):raise ValueError('數字的逗號格式不明確；請使用小數點、標準千分位，或改用 XLSX 數字儲存格。')
    s=s.replace(',','')
    try:v=float(s)
    except ValueError:return None
    return v if math.isfinite(v) else None

def timestamp(value,zone):
    s=str(value).strip()
    try:
        n=float(s)
        if 20000<n<100000:d=dt.datetime(1899,12,30)+dt.timedelta(days=n)
        elif n>1e9:return dt.datetime.fromtimestamp(n/(1000 if n>1e12 else 1),dt.timezone.utc).isoformat()
        else:raise ValueError()
    except ValueError:
        s=s.replace('/','-').replace('Z','+00:00')
        try:d=dt.datetime.fromisoformat(s)
        except ValueError:raise ValueError('無法解析交易時間：'+str(value)+'，請用 YYYY-MM-DD HH:mm:ss。')
    if d.tzinfo is None:
        tz=ZoneInfo(zone);a=d.replace(tzinfo=tz,fold=0);b=d.replace(tzinfo=tz,fold=1)
        if a.utcoffset()!=b.utcoffset():raise ValueError('夏令時間重複／不存在時刻須使用含 UTC 偏移的交易時間。')
        d=a
    return d.astimezone(dt.timezone.utc).isoformat()

def normalize(payload):
    zone=payload.get('timezone','UTC');ZoneInfo(zone)
    if payload.get('format')=='xlsx':rows=xlsx_rows(base64.b64decode(payload['data'],validate=True))
    else:
        text=payload.get('csv','').lstrip('\ufeff')
        if len(text)>12*1024*1024:raise ValueError('CSV 最大 12 MB。')
        dialect=csv.Sniffer().sniff(text[:5000],delimiters=',;\t') if text else csv.excel
        rows=list(csv.reader(io.StringIO(text),dialect))
    if len(rows)>100002:raise ValueError('交易檔上限 100,000 列。')
    aliases={'id':['trade #','trade number','trade','交易 #','交易編號','交易编号','交易序號','交易序号','交易號碼','交易号码'],
             'type':['type','類型','类型','方向'], 'time':['date/time','date and time','datetime','日期/時間','日期/时间','日期和時間','日期和时间','時間','时间'],
             'signal':['signal','訊號','信號','信号'], 'price':['price','price usd','價格','价格'],
             'qty':['contracts','contracts qty','position size qty','position size (qty)','數量','数量','合約','合约','倉位大小','仓位大小'],
             'pnl':['net p&l','net profit','profit','profit usd','淨損益','净损益','淨利','净利','損益','损益']}
    key=lambda x:re.sub(r'\s+',' ',str(x).strip().lower())
    header_index=next((i for i,row in enumerate(rows[:15]) if any(key(c) in aliases['type'] for c in row) and any(key(c) in aliases['time'] for c in row)),None)
    if header_index is None:raise ValueError('找不到 Type／Date and time 欄位。請匯出 TradingView「交易清單」，不是績效摘要。')
    headers=[key(c) for c in rows[header_index]];mapping={}
    for field,names in aliases.items():
        exact=next((i for i,h in enumerate(headers) if h in names),None)
        if exact is None and field in ('price','qty','pnl'):
            exact=next((i for i,h in enumerate(headers) if any(h.startswith(a+' ') or h.startswith(a+'(') or h.startswith(a+'（') for a in names) and not any(s in h for s in ['%','percent','百分','累計','累计','cumulative','run-up','drawdown'])),None)
        if exact is not None:mapping[field]=exact
    for field in ['id','type','time','price','qty']:
        if field not in mapping:raise ValueError('缺少交易欄位：'+field+'；請保留匯出的交易編號、類型、時間、價格、合約數量。')
    groups={};warnings=[]
    def cell(row,k):return row[mapping[k]] if k in mapping and mapping[k]<len(row) else ''
    for line,row in enumerate(rows[header_index+1:],header_index+2):
        if not any(row):continue
        kind=key(cell(row,'type'));entry=any(x in kind for x in ['entry','進場','入場','进场','入场']);exit_=any(x in kind for x in ['exit','出場','平倉','出场','平仓'])
        if not entry and not exit_:raise ValueError(f'第 {line} 列不是可識別的 Entry／Exit 類型。')
        if any(x in kind for x in ['short','空']):raise ValueError('此本地事件模型僅支援多頭，無法對帳空頭清單。')
        id=cell(row,'id');p=number(cell(row,'price'));q=number(cell(row,'qty'))
        if p is None or p<=0 or q is None or q<=0:raise ValueError(f'第 {line} 列價格或數量無效。')
        record=dict(time=timestamp(cell(row,'time'),zone),price=p,quantity=q,signal=cell(row,'signal'),netPnl=number(cell(row,'pnl')),row=line)
        group=groups.setdefault(id,{})
        part='entry' if entry else 'exit'
        if part in group:raise ValueError('交易 '+id+' 有多個 '+part+'，不能任意配對。')
        group[part]=record
    trades=[];opened=[]
    for id,g in groups.items():
        e=g.get('entry');x=g.get('exit')
        if not e:raise ValueError('交易 '+id+' 缺少入場列。')
        if not x:opened.append(dict(id=id,entryTime=e['time'],entryPrice=e['price'],quantity=e['quantity']));continue
        if dt.datetime.fromisoformat(x['time'])<dt.datetime.fromisoformat(e['time']):raise ValueError('交易 '+id+' 出場早於入場。')
        if abs(e['quantity']-x['quantity'])>1e-8:raise ValueError('交易 '+id+' 進出數量不同；請匯出完整配對的成交腿。')
        trades.append(dict(id=id,entryTime=e['time'],exitTime=x['time'],entryPrice=e['price'],exitPrice=x['price'],quantity=e['quantity'],netPnl=x['netPnl'] if x['netPnl'] is not None else e['netPnl'],signal=e['signal']))
    if not trades and not opened:raise ValueError('交易清單為空。')
    return dict(trades=trades,openTrades=opened,timezone=zone,headers=headers,warnings=warnings,rows=len(rows)-header_index-1)

if __name__=='__main__':
    try:print(json.dumps(normalize(json.load(sys.stdin)),ensure_ascii=False,allow_nan=False))
    except Exception as e:print(json.dumps(dict(error=str(e)),ensure_ascii=False));sys.exit(1)
