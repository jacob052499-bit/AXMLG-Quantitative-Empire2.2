import unittest,io,zipfile,base64
from xml.sax.saxutils import escape
from tradingview_import import normalize
HEADER='Trade #,Type,Date and time,Signal,Price USD,Position size (qty),Net P&L USD\n'
CSV=HEADER+'1,Exit long,2024-01-02 01:00:00,Exit,110,2,20\n1,Entry long,2024-01-01 01:00:00,Long,100,2,20\n2,Entry long,2024-01-03 01:00:00,Long,100,1,\n'
class ImportTests(unittest.TestCase):
    def test_paired_rows_exit_first_and_open_trade(self):
        r=normalize(dict(format='csv',csv=CSV,timezone='UTC'))
        self.assertEqual(len(r['trades']),1);self.assertEqual(len(r['openTrades']),1)
        self.assertEqual(r['trades'][0]['netPnl'],20);self.assertEqual(r['trades'][0]['quantity'],2)
    def test_explicit_timezone_converts_naive_times(self):
        r=normalize(dict(format='csv',csv=CSV,timezone='Asia/Taipei'))
        self.assertEqual(r['trades'][0]['entryTime'],'2023-12-31T17:00:00+00:00')
    def test_traditional_headers_and_quoted_thousands(self):
        s='交易編號,類型,日期/時間,訊號,價格 USD,數量,淨損益 USD\n1,出場做多,2024-01-02 00:00:00,Exit,"1,100",1,100\n1,進場做多,2024-01-01 00:00:00,Buy,"1,000",1,100\n'
        r=normalize(dict(format='csv',csv=s));self.assertEqual(r['trades'][0]['entryPrice'],1000)
    def test_summary_file_not_misread_as_trades(self):
        with self.assertRaisesRegex(ValueError,'交易清單'):normalize(dict(format='csv',csv='Metric,Value\nProfit,100'))
    def test_ambiguous_dst_and_duplicate_pair_fail(self):
        with self.assertRaisesRegex(ValueError,'夏令'):normalize(dict(format='csv',csv=CSV.replace('2024-01-01 01:00:00','2024-11-03 01:30:00'),timezone='America/New_York'))
        with self.assertRaisesRegex(ValueError,'多個'):normalize(dict(format='csv',csv=CSV+CSV.splitlines()[2]+'\n'))
    def test_no_short_or_invalid_quantities(self):
        with self.assertRaisesRegex(ValueError,'空頭'):normalize(dict(format='csv',csv=CSV.replace('Entry long','Entry short')))
        with self.assertRaisesRegex(ValueError,'數量'):normalize(dict(format='csv',csv=CSV.replace('Long,100,2','Long,100,0')))
        with self.assertRaisesRegex(ValueError,'逗號'):normalize(dict(format='csv',csv=CSV.replace('Long,100,2','Long,100,"0,5"')))
    def test_xlsx_inline_and_numeric_excel_dates(self):
        rows=[['Trade #','Type','Date and time','Signal','Price USD','Position size (qty)','Net P&L USD'],['1','Entry long','45292','Buy','100','2',''],['1','Exit long','45293','Sell','110','2','20']]
        sheet='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        for ri,row in enumerate(rows,1):
            sheet+='<row>'
            for ci,value in enumerate(row):sheet+=f'<c r="{chr(65+ci)}{ri}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            sheet+='</row>'
        sheet+='</sheetData></worksheet>';data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:z.writestr('xl/worksheets/sheet1.xml',sheet)
        r=normalize(dict(format='xlsx',data=base64.b64encode(data.getvalue()).decode(),timezone='UTC'))
        self.assertEqual(r['trades'][0]['entryTime'],'2024-01-01T00:00:00+00:00')
        self.assertEqual(r['trades'][0]['netPnl'],20)
if __name__=='__main__':unittest.main()
