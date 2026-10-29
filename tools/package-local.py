"""Customer-only allowlist. No merchant credentials, private key, test bypass, or issuer tools."""
from pathlib import Path
import zipfile,hashlib,json
ROOT=Path(__file__).resolve().parent.parent
DEST=ROOT/'dist'; DEST.mkdir(exist_ok=True)
NAME='AXMLG-Quantitative-Empire-Local-Windows-x64.zip'; PREFIX='AXMLG Quantitative Empire/'
entries={}
for tree in ['app','lib','python','examples','desktop','.python','.desktop/runtime-v45.0.0']:
    for item in (ROOT/tree).rglob('*'):
        relative=item.relative_to(ROOT)
        if item.is_file() and '__pycache__' not in relative.parts and item.suffix not in ('.pyc','.pyo') and not item.name.startswith('test_'):
            entries[str(relative).replace('\\','/')]=item
for name in ['package.json','server.mjs','THIRD-PARTY-NOTICES.txt','start.bat','啟動APP.cmd','安裝桌面捷徑.cmd','tools/launch.mjs','tools/install-desktop.ps1','tools/build-user-guide.mjs','docs/user-guide-content.mjs','docs/factor-literature.json','docs/AXMLG Quantitative Empire 使用教學.html','data/.gitkeep']:
    item=ROOT/name
    if not item.is_file(): raise ValueError('Missing '+name)
    entries[name]=item
config=dict(product='AXMLG Quantitative Empire',version='2.3.0',mode='standalone-local',profile='portable',port=4312,permanent=True,requiresLicense=False,requiresLogin=False)
manifest=dict(product=config['product'],version=config['version'],edition='standalone-local',containsAccounts=False,containsSetupToken=False,containsPrivateKey=False,containsPrivateResearch=False,files=len(entries)+3,permanent=True,requiresLicense=False,requiresLogin=False)
with zipfile.ZipFile(DEST/(NAME+'.part'),'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for name,item in sorted(entries.items()): z.write(item,PREFIX+name)
    z.writestr(PREFIX+'distribution.json',json.dumps(config,ensure_ascii=False,indent=2))
    z.writestr(PREFIX+'RELEASE-MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    z.writestr(PREFIX+'README.txt','AXMLG Quantitative Empire 本機研究版 2.3.0\n\n1. 完整解壓縮後執行「啟動APP.cmd」。\n2. 開啟即可直接進入研究工作台，不需帳號密碼。\n3. 可執行「安裝桌面捷徑.cmd」建立本機版捷徑。\n4. 教學不需登入：開啟 app/user-guide.html。\n\n直接離線使用，無登入或啟用程序。\n回測與策略保存在 data，請自行備份。只執行可信的 Python 策略，本機程式具備目前 Windows 使用者的檔案權限。\n'.encode('utf-8'))
with zipfile.ZipFile(DEST/(NAME+'.part')) as z:
    assert z.testzip() is None
    names=[n.removeprefix(PREFIX) for n in z.namelist()]
    assert [n for n in names if n.startswith('data/')]==['data/.gitkeep']
    assert not any(n.startswith(('.runtime/','tests/','deploy/','dist/')) or n.endswith(('.pem','owner.mjs','package-local.py','start-research-server.mjs')) for n in names)
    assert not any(('axmlg'+'_edge.py') in n for n in names)
(DEST/(NAME+'.part')).replace(DEST/NAME)
with (DEST/NAME).open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
manifest.update(archive=NAME,sha256=digest,bytes=(DEST/NAME).stat().st_size,zipCrcPassed=True)
(DEST/'local-release.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
(DEST/'LOCAL-SHA256SUMS.txt').write_text(digest+'  '+NAME+'\n',encoding='utf-8')
print(json.dumps(dict(archive=NAME,bytes=manifest['bytes'],sha256=digest)))
