@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 小说简介查询 - 晋江 / 长佩

python -V >nul 2>nul
if not errorlevel 1 goto run_python

py -V >nul 2>nul
if not errorlevel 1 goto run_py

echo [错误] 没有找到 Python，请先安装 Python 3.9 或更高版本：
echo        https://www.python.org/downloads/
echo        安装时请勾选 "Add python.exe to PATH"。
echo.
echo 提示：也可以直接双击同目录下的「小说简介查询.exe」，不需要 Python。
echo.
pause
exit /b 1

:run_python
python app.py --window
goto done

:run_py
py app.py --window
goto done

:done
echo.
echo 小程序已退出。
pause
