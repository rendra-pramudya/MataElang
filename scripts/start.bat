@echo off
REM ---------------------------------------------------------------------------
REM MataElang - start the Python side (FastAPI + uvicorn).
REM
REM   start.bat                 live mode on port 8000
REM   start.bat fixture         fixture mode - no network calls, fixtures only
REM   start.bat 8001            live mode on a different port
REM   start.bat fixture 8001    both
REM
REM This starts FastAPI only. The 120 GB planet.pmtiles is served by Caddy, not
REM by uvicorn - run `caddy run` alongside this and open http://localhost:8080.
REM Without Caddy, open http://localhost:8000 directly: markers work, the
REM basemap stays blank.
REM
REM Safe to double-click. The window stays open on error so you can read it.
REM ---------------------------------------------------------------------------

setlocal
cd /d "%~dp0.."

REM MODE=env means "whatever FIXTURE_MODE says in .env" - we do not override it,
REM and we must not claim otherwise in the banner below.
set "PORT=8000"
set "MODE=env"

REM -- arguments ---------------------------------------------------------------
:parse
if "%~1"=="" goto parsed
if /i "%~1"=="fixture" goto arg_fixture
if /i "%~1"=="live" goto arg_live
echo %~1| findstr /r "^[0-9][0-9]*$" >nul
if not errorlevel 1 goto arg_port
echo [MataElang] Unknown argument: %~1
echo             Usage: start.bat [fixture^|live] [port]
goto fail

:arg_fixture
set "FIXTURE_MODE=true"
set "MODE=fixture"
shift
goto parse

:arg_live
set "FIXTURE_MODE=false"
set "MODE=live"
shift
goto parse

:arg_port
set "PORT=%~1"
shift
goto parse

:parsed

REM -- uv ----------------------------------------------------------------------
where uv >nul 2>nul
if errorlevel 1 (
    echo [MataElang] uv is not on PATH - everything here runs through it.
    echo             Install:  powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
    echo             Then open a NEW terminal so PATH picks it up.
    goto fail
)

REM -- .env --------------------------------------------------------------------
REM Free API keys and feed URLs live here, never in code (CLAUDE.md rule 1).
if not exist ".env" (
    if exist ".env.example" (
        echo [MataElang] No .env found - seeding it from .env.example
        copy /y ".env.example" ".env" >nul
    ) else (
        echo [MataElang] Warning: no .env and no .env.example - using built-in defaults.
    )
)

REM -- dependencies ------------------------------------------------------------
echo [MataElang] Syncing dependencies...
call uv sync
if errorlevel 1 (
    echo [MataElang] uv sync failed - see the error above.
    goto fail
)

REM -- go ----------------------------------------------------------------------
echo.
echo   MataElang - port %PORT%
if /i "%MODE%"=="fixture" echo   Mode: FIXTURE - no network calls, fixture data only.
if /i "%MODE%"=="live"    echo   Mode: LIVE - real sources.
if /i "%MODE%"=="env"     echo   Mode: whatever FIXTURE_MODE says in .env
echo   FastAPI  http://localhost:%PORT%
echo   With Caddy running, use http://localhost:8080 for the basemap.
echo   Ctrl+C to stop.
echo.

REM Bound to 127.0.0.1 on purpose: MataElang is a single-user monitor and has no
REM authentication. Change this only if you know what you are exposing.
call uv run uvicorn mataelang.main:app --host 127.0.0.1 --port %PORT%
set "RC=%ERRORLEVEL%"

REM A clean Ctrl+C shutdown is not a failure - only pause when something broke,
REM so the window stays up long enough to read "port already in use".
if not "%RC%"=="0" (
    echo.
    echo [MataElang] uvicorn exited with code %RC%.
    echo             Port %PORT% already in use? Try:  start.bat 8001
    pause
)
endlocal
exit /b %RC%

:fail
echo.
pause
endlocal
exit /b 1
