@echo off
echo CPEEN 2026 – Build Proctor Agent
echo =================================

pip install -r requirements.txt

pyinstaller ^
    --onefile ^
    --windowed ^
    --name "CPEEN_Proctor" ^
    --icon NOICON ^
    proctor.py

echo.
echo Build complet. Fisierul se afla in: dist\CPEEN_Proctor.exe
pause
