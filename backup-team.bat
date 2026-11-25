@echo off
chcp 65001 >nul 2>nul
rem 团队版数据备份：调用 backup.py 做一致性快照。
rem
rem 为什么不再用 copy /Y geo.db：
rem   SQLite 在 WAL 模式下，已提交的数据可能还留在 -wal 文件里没写进主文件。
rem   直接复制主文件会得到一份"能打开、能查询、只是少了最近几条"的备份——
rem   不会报错，等真需要它的时候才发现缺了关键的一段。
rem   backup.py 用 VACUUM INTO，由 SQLite 自己生成一致快照，服务开着也能备份。
rem
rem 换回方法：关掉服务窗口，然后
rem   python backend\scripts\backup.py --list                      看有哪几份
rem   python backend\scripts\backup.py --restore backend\backups\某一份.db
rem （恢复前会自动把当前库存成 pre-restore-*，选错了还能退回去）

cd /d "%~dp0"

set "PY="
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto run
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto run
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if exist "C:\Program Files\Python312\python.exe" set "PY=C:\Program Files\Python312\python.exe"
if defined PY goto run

echo   [x] 没找到 Python。装一个 3.10+ 的再来。
pause
exit /b 1

:run
%PY% backend\scripts\backup.py --keep 14
if errorlevel 1 (
    echo   [x] 备份失败，看上面的提示。
    pause
    exit /b 1
)

echo.
echo   备份完成。恢复方法见本文件开头的注释，或看 README 的「备份与恢复」。
pause
exit /b 0
