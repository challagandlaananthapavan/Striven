$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonCandidates = @()

if ($env:VIRTUAL_ENV) {
    $pythonCandidates += Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
}

$pythonCandidates += Join-Path (Split-Path $projectRoot -Parent) ".venv\Scripts\python.exe"
$pythonCandidates += Join-Path $projectRoot ".venv\Scripts\python.exe"
$pythonPath = $null

foreach ($candidate in $pythonCandidates) {
    if (-not (Test-Path $candidate)) {
        continue
    }

    & $candidate -c "import sys; print(sys.executable)" *> $null
    if ($LASTEXITCODE -eq 0) {
        $pythonPath = $candidate
        break
    }
}

if (-not $pythonPath) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        & $pythonCommand.Source -c "import sys; print(sys.executable)" *> $null
        if ($LASTEXITCODE -eq 0) {
            $pythonPath = $pythonCommand.Source
        }
    }
}

if (-not $pythonPath) {
    throw "Python was not found. Activate the project's virtual environment or install the project dependencies first."
}

$previousCorsOrigins = $env:STRIVEN_CORS_ORIGINS
$env:STRIVEN_CORS_ORIGINS = "https://strivenrag.netlify.app,http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173"

try {
    Set-Location $projectRoot
    Write-Host "Starting the API on http://127.0.0.1:8000. Keep this window open."
    & $pythonPath -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --workers 1
    if ($LASTEXITCODE -ne 0) {
        throw "The API exited with code $LASTEXITCODE."
    }
}
finally {
    if ($null -eq $previousCorsOrigins) {
        Remove-Item Env:\STRIVEN_CORS_ORIGINS -ErrorAction SilentlyContinue
    }
    else {
        $env:STRIVEN_CORS_ORIGINS = $previousCorsOrigins
    }
}
