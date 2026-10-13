$ErrorActionPreference = "Stop"

$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflared) {
    throw "cloudflared is not installed or is not on PATH. Install the official Windows MSI from https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/, then open a new PowerShell window."
}

Write-Host "Starting a temporary public HTTPS tunnel to http://127.0.0.1:8000."
Write-Host "After Cloudflare prints the trycloudflare.com URL, set that URL as VITE_API_BASE_URL in Netlify and redeploy."
Write-Host "Keep this window open; stopping it disables public API access."

& $cloudflared.Source tunnel --url http://127.0.0.1:8000
if ($LASTEXITCODE -ne 0) {
    throw "cloudflared exited with code $LASTEXITCODE."
}
