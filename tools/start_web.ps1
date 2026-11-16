$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$data = if ($env:KANDU_WEB_DATA_DIR) { $env:KANDU_WEB_DATA_DIR } else { Join-Path $env:LOCALAPPDATA 'KanduWeb' }
$python = Join-Path $repo '.venv\Scripts\python.exe'
$url = 'http://127.0.0.1:8766'
try {
    $health = $null
    try { $health = Invoke-RestMethod -Uri ($url + '/api/health') -TimeoutSec 2 } catch {}
    if (-not $health -or $health.status -ne 'ok' -or -not $health.version) {
        if (-not (Test-Path -LiteralPath $python)) { throw 'Install requirements-web.txt first. See WEB.md.' }
        if (Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 8766 is occupied by another application.' }
        New-Item -ItemType Directory -Path (Join-Path $repo 'dist') -Force | Out-Null
        $arguments = @('-u', ('"' + (Join-Path $repo 'run_web.py') + '"'), '--data-dir', ('"' + $data + '"'))
        $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $repo 'dist\web-server.log') -RedirectStandardError (Join-Path $repo 'dist\web-server-error.log')
        $process.Id | Set-Content -LiteralPath (Join-Path $repo 'dist\web-server.pid')
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            Start-Sleep -Milliseconds 300
            try { $health = Invoke-RestMethod -Uri ($url + '/api/health') -TimeoutSec 1; break } catch {}
            if ($process.HasExited) { throw 'Server exited. See dist\web-server-error.log.' }
        }
        if (-not $health) { throw 'Server is still starting. See dist\web-server.log.' }
    }
    Write-Host ('Kandu Web: ' + $url)
    Write-Host ('Access password: ' + (Get-Content -LiteralPath (Join-Path $data 'access-code.txt') -Raw).Trim())
    Write-Host 'The server runs in the background; closing this launcher does not stop it.'
    Start-Process $url
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
