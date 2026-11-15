@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 exit /b 1
set "UV_CACHE_DIR=%~dp0.cache\uv"
set "UV_PYTHON_INSTALL_DIR=%~dp0.cache\python"
set "PIP_CACHE_DIR=%~dp0.cache\pip"
set "HF_HOME=%~dp0.cache\huggingface"
set "TORCH_HOME=%~dp0.cache\torch"
set "TEMP=%~dp0.cache\tmp"
set "TMP=%~dp0.cache\tmp"
set "UV_PROJECT_ENVIRONMENT=%~dp0.venv"
for %%D in ("%UV_CACHE_DIR%" "%UV_PYTHON_INSTALL_DIR%" "%PIP_CACHE_DIR%" "%HF_HOME%" "%TORCH_HOME%" "%TEMP%") do (
    if not exist "%%~D" mkdir "%%~D"
    if not exist "%%~D" (
        echo "Cannot create project cache directory: %%~D"
        exit /b 1
    )
)
if not exist ".venv\Scripts\pythonw.exe" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
    if errorlevel 1 exit /b 1
)
if not exist ".venv\Scripts\pythonw.exe" (
    echo Setup did not create .venv\Scripts\pythonw.exe.
    exit /b 1
)
start "" /d "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" -m tingmu gui %*
exit /b %errorlevel%
