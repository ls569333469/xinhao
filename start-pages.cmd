@echo off
"C:\Python314\python.exe" -X utf8 "%~dp0start-pages.py"
if errorlevel 1 (
    pause
    exit /b 1
)
start "" "http://127.0.0.1:8767/"
start "" "http://127.0.0.1:8768/prototype.html"
