$ErrorActionPreference = 'Stop'
$taskRuntime = 'F:\chatbot-runtime\AstrBot'
$taskDataRoot = Join-Path $env:LOCALAPPDATA 'CompanionCore\runtime'
$taskProcessFile = Join-Path $taskDataRoot 'data\companion-process.local.json'
if (Test-Path -LiteralPath $taskProcessFile) {
    $taskSaved = Get-Content -LiteralPath $taskProcessFile -Raw | ConvertFrom-Json
    $taskExisting = Get-Process -Id $taskSaved.id -ErrorAction SilentlyContinue
    if ($taskExisting -and $taskExisting.StartTime.ToUniversalTime().Ticks -eq $taskSaved.started_ticks) {
        Write-Output 'AstrBot is already running: http://127.0.0.1:6185'
        exit 0
    }
}
if (Get-NetTCPConnection -LocalPort 6185 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 6185 is already used; no additional instance started.'
}
$env:ASTRBOT_ROOT = $taskDataRoot
$env:PYTHONUTF8 = '1'
$taskProcess = Start-Process -FilePath (Join-Path $taskRuntime '.venv\Scripts\python.exe') -ArgumentList @('main.py') -WorkingDirectory $taskRuntime -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskDataRoot 'data\stdout.local.log') -RedirectStandardError (Join-Path $taskDataRoot 'data\stderr.local.log') -PassThru
@{ id = $taskProcess.Id; started_ticks = $taskProcess.StartTime.ToUniversalTime().Ticks } | ConvertTo-Json | Set-Content -LiteralPath $taskProcessFile
$taskReady = $false
for ($taskAttempt = 0; $taskAttempt -lt 40; $taskAttempt++) {
    if ($taskProcess.HasExited) { throw 'AstrBot exited during startup; inspect local stderr log.' }
    try {
        $taskResponse = Invoke-WebRequest -Uri 'http://127.0.0.1:6185/' -TimeoutSec 1 -UseBasicParsing
        if ($taskResponse.StatusCode -eq 200) { $taskReady = $true; break }
    } catch { }
    Start-Sleep -Milliseconds 500
}
if (-not $taskReady) { throw 'AstrBot did not become ready within the startup check window.' }
Write-Output 'AstrBot is ready: http://127.0.0.1:6185'
