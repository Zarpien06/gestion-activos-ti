@echo off
python -m pip install -r requirements.txt
pyinstaller --onefile --windowed --name RecolectorInventario recolector.py
echo EXE creado en dist\RecolectorInventario.exe
pause
