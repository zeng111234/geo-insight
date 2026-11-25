@echo off
chcp 65001 >nul 2>nul
rem 团队版数据备份：把 geo.db 按时间戳复制一份到 backend\backups\。
rem 数据库是唯一副本（就在这台机器上），定期双击一次；出问题拿最近的备份换回去。
rem 换回方法：关掉服务黑窗口，把 backups 里最近一份复制回 backend\geo.db，再双击 start-team.bat。

cd /d "%~dp0backend"

set "TS="
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "TS=%%a"
if not defined TS set "TS=manual"

if not exist "backups" mkdir backups
copy /Y "geo.db" "backups\geo-%TS%.db" >nul
if errorlevel 1 (
    echo   [x] 备份失败：geo.db 不存在，或服务正在写入（先关掉服务再备份最稳）。
    pause
    exit /b 1
)

echo   已备份：backups\geo-%TS%.db
echo   备份是整库快照，多了自己删旧的。
pause
exit /b 0
