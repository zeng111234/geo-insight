@echo off
chcp 65001 >nul 2>nul
rem 团队启动器：把服务绑到局域网/内网给同事用（区别于 start.bat 的本机免登录演示模式）。
rem
rem 首次使用（只需一次）：
rem   1. 建文件 backend\data\team.local.bat，写一行（该路径已被 .gitignore 覆盖，不会提交）：
rem        set GEO_ADMIN_PASSWORD=你们的共享密码
rem   2. Windows 防火墙放行端口（管理员 PowerShell，一次性）：
rem        netsh advfirewall firewall add rule name="GEO Insight" dir=in action=allow protocol=TCP localport=8123
rem   3. 双击本文件，把窗口里打印的 http://<本机IP>:8123 发给同事收藏。
rem
rem 更新代码给同事：在本机 git pull 之后关掉黑窗口再双击本文件即可，同事刷新页面。
rem 同机双开（你的开发版占 8123）：在 team.local.bat 里再加一行 set GEO_PORT=8124。
rem 注意：共享实例上不要用 --reset（会清空真实数据重灌演示数据）。

cd /d "%~dp0backend"

rem 密码从 gitignore 掉的本地文件读；没配就落到 settings 的默认值并提醒。
if exist "%~dp0backend\data\team.local.bat" call "%~dp0backend\data\team.local.bat"
if not defined GEO_ADMIN_PASSWORD echo   [提示] 未配置 GEO_ADMIN_PASSWORD（backend\data\team.local.bat），当前使用默认密码。

set "PY="
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto run

where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto run

if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if exist "C:\Program Files\Python312\python.exe" set "PY=C:\Program Files\Python312\python.exe"
if defined PY goto run

echo.
echo   Python 3.10+ was not found on this computer.
echo   Install it from https://www.python.org/downloads/
echo   and tick "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1

:run
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
rem 团队模式：开鉴权（GEO_AUTH_REQUIRED 不设即默认开）、绑所有网卡。
set GEO_AUTH_REQUIRED=1
set GEO_HOST=0.0.0.0

echo   本机局域网 IP（发给同事的地址从这里挑一个）：
ipconfig | findstr /C:"IPv4"
echo.

%PY% run.py --no-browser %*
if errorlevel 1 pause
exit /b 0
