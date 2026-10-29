@echo off
setlocal
cd /d "%~dp0"
if exist ".desktop\runtime-v45.0.0\QuantitativeEmpire.exe" (
  start "" "%~dp0.desktop\runtime-v45.0.0\QuantitativeEmpire.exe"
  exit /b 0
)
echo Please retain the complete AXMLG Quantitative Empire folder.
pause
