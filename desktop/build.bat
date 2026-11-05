@echo off
cd /d "%~dp0"
rem Build the program into an exe. Result: dist\agolate\agolate.exe

if exist ".venv\installed.ok" goto build
echo Run run.bat once first: it installs everything needed.
pause
exit /b 1

:build
echo Installing PyInstaller...
".venv\Scripts\python.exe" -m pip install --upgrade pyinstaller
if errorlevel 1 goto fail

echo.
echo Building exe, this takes 1-3 minutes...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --name agolate --icon icon.ico --add-data "icon.png;." --collect-all winrt --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 --exclude-module torch --exclude-module torchvision --exclude-module easyocr main.py
if errorlevel 1 goto fail

xcopy /E /I /Y "glossaries" "dist\agolate\glossaries" >nul
if exist "fonts" xcopy /E /I /Y "fonts" "dist\agolate\fonts" >nul
if exist "config.json" copy /Y "config.json" "dist\agolate\config.json" >nul

echo.
echo DONE. The program is in the folder dist\agolate
echo Run agolate.exe. You can move or copy this whole folder anywhere.
explorer "dist\agolate"
pause
exit /b 0

:fail
echo.
echo ERROR: build failed. Copy the messages above and send them.
pause
exit /b 1
