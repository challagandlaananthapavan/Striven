$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$apiUrl = "http://127.0.0.1:8000/api/health"
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

function Test-LocalApiHealth {
    $request = [System.Net.HttpWebRequest]::Create($apiUrl)
    $request.Method = "GET"
    $request.Timeout = 1500
    $response = $null

    try {
        $response = $request.GetResponse()
        $reader = [System.IO.StreamReader]::new($response.GetResponseStream())
        try {
            $body = $reader.ReadToEnd()
        }
        finally {
            $reader.Dispose()
        }

        return $response.StatusCode -eq [System.Net.HttpStatusCode]::OK -and $body -match '"status"\s*:\s*"ok"'
    }
    catch [System.Net.WebException] {
        return $false
    }
    finally {
        if ($response) {
            $response.Dispose()
        }
    }
}

if (Test-LocalApiHealth) {
    Write-Host "API is already running and healthy at http://127.0.0.1:8000."
}
else {
    if ($portInUse) {
        throw "Port 8000 is occupied by a service that did not pass the API health check. Stop that service before starting Striven."
    }

    $apiScript = Join-Path $PSScriptRoot "start-local-api.ps1"
    $quotedApiScript = '"' + $apiScript + '"'
    Write-Host "Starting the API in a separate PowerShell window..."
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-NoExit -ExecutionPolicy Bypass -File $quotedApiScript" `
        -WorkingDirectory $projectRoot | Out-Null

    $apiReady = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Seconds 1
        if (Test-LocalApiHealth) {
            $apiReady = $true
            break
        }
    }

    if (-not $apiReady) {
        throw "The API did not become healthy within 60 seconds. Check the separate API PowerShell window, then run this command again."
    }
}

Write-Host "API health check passed. Starting the public Cloudflare Tunnel in this window..."
Write-Host "When Cloudflare prints a trycloudflare.com URL, set it as VITE_API_BASE_URL in Netlify and redeploy if needed."

$tunnelScript = Join-Path $PSScriptRoot "start-cloudflare-tunnel.ps1"
& $tunnelScript
if ($LASTEXITCODE -ne 0) {
    throw "The Cloudflare Tunnel exited with code $LASTEXITCODE."
}
