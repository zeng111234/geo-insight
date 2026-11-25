@echo off
chcp 65001 >nul 2>nul
rem 团队版数据备份：把 geo.db 按时间戳复制一份到 backend\backups\。
rem 数据库是唯一副本（就在这台机器上），定期双击一次；出问题拿最近的备份换回去。
rem 换回方法：关掉服务黑窗口，把 backups 里最近一份复制回项目根的 geo.db，再双击 start-team.bat。
rem
rem 2026-11-25 修：库原来在 backend\geo.db（因为 start.bat 先 cd backend），现在固定在
rem 项目根（settings 用 PROJECT_ROOT 推路径，不跟 cwd 走）。所以不能再 cd backend
rem 后用相对 geo.db，那样备份会静默找不到文件。

cd /d "%~dp0"

set "TS="
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "TS=%%a"
if not defined TS set "TS=manual"

if not exist "backend\backups" mkdir "backend\backups"
copy /Y "geo.db" "backend\backups\geo-%TS%.db" >nul
if errorlevel 1 (
    echo   [x] 备份失败：项目根目录没有 geo.db，或服务正在写入（先关掉服务再备份最稳）。
    pause
    exit /b 1
)

echo   已备份：backend\backups\geo-%TS%.db
echo   备份是整库快照，多了自己删旧的。
pause
exit /b 0
