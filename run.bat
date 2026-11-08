@echo off
setlocal
cd /d "%~dp0"
py -3 --version >nul 2>&1
if errorlevel 1 goto try_python
py -3 lottery_tool.py %*
set task_result=%errorlevel%
goto finished
:try_python
python --version >nul 2>&1
if errorlevel 1 goto missing
python lottery_tool.py %*
set task_result=%errorlevel%
goto finished
:missing
echo Install Python 3.10 or newer from python.org, then run this file again.
set task_result=2
:finished
if "%~1"=="" pause
exit /b %task_result%
