$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$portProbe = [System.Net.Sockets.TcpClient]::new()
$portInUse = $false

try {
    $connectTask = $portProbe.ConnectAsync("127.0.0.1", 8000)
    try {
        $portInUse = $connectTask.Wait(1000) -and $portProbe.Connected
    }
    catch [System.AggregateException] {
        $portInUse = $false
    }
}
finally {
    $portProbe.Dispose()
}

if ($portInUse) {
    $healthRequest = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:8000/api/health")
    $healthRequest.Method = "GET"
    $healthRequest.Timeout = 3000
    $healthResponse = $null

    try {
        $healthResponse = $healthRequest.GetResponse()
        $reader = [System.IO.StreamReader]::new($healthResponse.GetResponseStream())
        try {
            $healthBody = $reader.ReadToEnd()
        }
        finally {
            $reader.Dispose()
        }

        if ($healthResponse.StatusCode -eq [System.Net.HttpStatusCode]::OK -and $healthBody -match '"status"\s*:\s*"ok"') {
            Write-Host "The API is already running and healthy at http://127.0.0.1:8000. No second copy was started."
            return
        }

        throw "Port 8000 is occupied, but the service did not return the expected API health response. Stop the conflicting process or configure another port."
    }
    catch [System.Net.WebException] {
        throw "Port 8000 is occupied, but the API health check failed. Stop the conflicting process or check its logs before starting this API."
    }
    finally {
        if ($healthResponse) {
            $healthResponse.Dispose()
        }
    }
}

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
