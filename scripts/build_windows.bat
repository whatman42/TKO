@echo off
setlocal
cd /d "%~dp0\.."
python -m pip install --upgrade pip
python -m pip install pyinstaller
if exist requirements.txt python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm tko.spec
echo Output: dist\TKO\TKO.exe
endlocal
