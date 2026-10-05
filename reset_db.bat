@echo off
title Reiniciar Base de Datos - Condominium Manager
echo =======================================================
echo REINICIANDO BASE DE DATOS A BLANCO
echo =======================================================
echo.
set /p CONFIRM="¿Esta seguro de que desea borrar TODOS los datos de la base de datos? (S/N): "
if /i "%CONFIRM%" neq "S" (
    echo.
    echo Operacion cancelada. No se modifico la base de datos.
    echo.
    pause
    exit /b
)

set PYTHON_CMD=python.exe

if exist C:\condomanager\venv\Scripts\python.exe (
    set PYTHON_CMD=C:\condomanager\venv\Scripts\python.exe
) else if exist venv\Scripts\python.exe (
    set PYTHON_CMD=venv\Scripts\python.exe
) else if exist ..\venv\Scripts\python.exe (
    set PYTHON_CMD=..\venv\Scripts\python.exe
)

%PYTHON_CMD% reset_db.py
echo.
echo Presione cualquier tecla para salir...
pause > nul
