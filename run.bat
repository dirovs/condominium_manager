@echo off
title CondoManager Dev Server
echo ==============================================
echo       INICIANDO SERVIDOR CONDOMINIO
echo ==============================================
echo.
cd /d "%~dp0"

set TESTING=
set PYTHON_CMD=python.exe

if exist C:\condomanager\venv\Scripts\python.exe (
    set PYTHON_CMD=C:\condomanager\venv\Scripts\python.exe
) else if exist venv\Scripts\python.exe (
    set PYTHON_CMD=venv\Scripts\python.exe
) else if exist ..\venv\Scripts\python.exe (
    set PYTHON_CMD=..\venv\Scripts\python.exe
)

%PYTHON_CMD% -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
if %errorlevel% neq 0 (
    echo.
    echo Ocurrio un error al iniciar el servidor.
    echo Asegurate de que el entorno virtual este correcto o activo.
    pause
)
