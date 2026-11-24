@echo off
chcp 65001 >nul 2>nul
rem 团队版「更新按钮」：git pull + 自动重启团队实例。放在 stable 文件夹根目录，双击即完成。
rem 同事什么都不用做，刷新页面即可（要重新登录一次）。
rem 前提：stable 文件夹是 git clone 出来的，且第一次 pull 已输入过凭据（Windows 凭据管理器会记住）。
rem 更新纪律：只把验证过的代码 push 到 master，stable 只跟 master（git 分支本身就是版本清单，
rem 不需要额外的 JSON 更新描述文件）。

cd /d "%~dp0"

rem 端口跟 start-team.bat 读同一份配置，别各配各的
if exist "%~dp0backend\data\team.local.bat" call "%~dp0backend\data\team.local.bat"
if not defined GEO_PORT set "GEO_PORT=8123"

echo [1/3] 拉取最新稳定版（master）...
git pull --ff-only
if errorlevel 1 (
    echo.
    echo   [x] 拉取失败：网络问题，或 stable 文件夹里有本地改动。
    echo       stable 文件夹永远不要手改文件，改代码请在开发版改完 push。
    pause
    exit /b 1
)

echo [2/3] 停掉团队实例（按端口找进程，不影响你 8123 的开发版）...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr /C:":%GEO_PORT% " ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>nul

echo [3/3] 重启团队实例...
start "GEO 团队版" "%~dp0start-team.bat"

echo.
echo 完成。同事刷新页面、重新登录一次即可。
timeout /t 5 >nul
exit /b 0
