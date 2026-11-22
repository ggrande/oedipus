@echo off
powershell.exe -NoProfile -File "%~dp0Install-ChildFix.ps1" %*
exit /b %ERRORLEVEL%
