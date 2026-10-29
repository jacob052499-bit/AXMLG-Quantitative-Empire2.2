# AXMLG Quantitative Empire 2.3

直接使用的本機量化研究 App，沒有帳號密碼、登入、管理員或啟用程序。

完整解壓縮後執行「啟動APP.cmd」。桌面捷徑仍為 AXMLG Quantitative Empire；客戶 ZIP 可執行「安裝桌面捷徑.cmd」建立本機版捷徑。內附桌面引擎、Python、NumPy 與 pandas。

## 研究與升級

策略筆記、Python 回測、PP、MC、因子研究與批次回測均在本機執行。舊研究仍在原本資料夾；local-workspace.json 記錄沿用的研究位置，瀏覽器草稿延用原本命名。其他歷史工作空間也保留，不自動混合或刪除。請備份整個 data。

本機只綁定 loopback。保留主機／來源驗證、JSON 輸入限制及頁面請求驗證，防止其他網站直接發送研究操作。這些不需要使用者設定帳密。重啟引擎後若舊頁面提示更新，重新整理即可。

## 分享與下載

只分享 dist/AXMLG-Quantitative-Empire-Local-Windows-x64.zip。allowlist 發行包不含你的帳號、舊驗證資料、策略、研究、日誌或備份。自己的營運資料夾不可直接寄給他人。

portal-cloud 是獨立公開下載網站來源，不要求登入，也不接受研究工作；研究留在客戶電腦。公開雲端部署尚未完成，本機更新不代表已上架。

## 容量與安全

預設 500 個保存策略、20 GiB 本機研究儲存、每工作空間一個同時 Python 工作；依設備容量調整。詳見 docs/商用版架構與安全檢查.md。

Python 具有目前 Windows 使用者的權限，僅執行可信策略。教學 app/user-guide.html 可直接閱讀，完整離線教學在 docs/AXMLG Quantitative Empire 使用教學.html。第三方軟體授權保留於 THIRD-PARTY-NOTICES.txt。

驗證：node --test tests/*.test.mjs；隱私檢查：node tools/release-check.mjs；乾淨打包：python tools/package-local.py；實際解壓驗收：node tools/verify-local.mjs。
