@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0rollback_old_ui.ps1" %*
