$ErrorActionPreference = 'Stop'
$taskGuard = New-Object System.Threading.Mutex($false, 'Local\CompanionCoreLifecycle')
$taskHeld = $false
try {
try { $taskHeld = $taskGuard.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $taskHeld = $true }
if (-not $taskHeld) { Write-Output 'Companion Core startup or recovery is already in progress.'; exit 0 }
$taskRuntime = 'F:\chatbot-runtime\AstrBot'
$taskRootConfig = Join-Path $PSScriptRoot '..\config\runtime_root.local.json'
$taskDataRoot = if (Test-Path -LiteralPath $taskRootConfig) { (Get-Content -LiteralPath $taskRootConfig -Raw -Encoding UTF8 | ConvertFrom-Json).runtime_root } else { Join-Path $env:LOCALAPPDATA 'CompanionCore\runtime' }
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
$taskLogRoot = Join-Path $taskDataRoot 'data\companion_logs'
$taskBase = [IO.Path]::GetFullPath((Join-Path $taskDataRoot 'data')).TrimEnd('\') + '\'
$taskLogFull = [IO.Path]::GetFullPath($taskLogRoot)
if (-not $taskLogFull.StartsWith($taskBase, [StringComparison]::OrdinalIgnoreCase)) { throw 'Log archive path escaped the private data directory.' }
New-Item -ItemType Directory -Path $taskLogRoot -Force | Out-Null
$taskLogStamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff') + '-' + [Guid]::NewGuid().ToString('N')
foreach ($taskLogName in @('stdout', 'stderr')) {
    $taskOldLog = Join-Path $taskDataRoot "data\$taskLogName.local.log"
    if (Test-Path -LiteralPath $taskOldLog) {
        $taskArchiveLog = Join-Path $taskLogRoot "$taskLogName-$taskLogStamp.local.log"
        Move-Item -LiteralPath $taskOldLog -Destination $taskArchiveLog
    }
}
$taskBootstrap = Join-Path $PSScriptRoot 'runtime_bootstrap.py'
$taskProcess = Start-Process -FilePath (Join-Path $taskRuntime '.venv\Scripts\python.exe') -ArgumentList @($taskBootstrap) -WorkingDirectory $taskRuntime -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskDataRoot 'data\stdout.local.log') -RedirectStandardError (Join-Path $taskDataRoot 'data\stderr.local.log') -PassThru
@{ id = $taskProcess.Id; started_ticks = $taskProcess.StartTime.ToUniversalTime().Ticks } | ConvertTo-Json | Set-Content -LiteralPath $taskProcessFile -Encoding UTF8
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
} finally {
    if ($taskHeld) { $taskGuard.ReleaseMutex() }
    $taskGuard.Dispose()
}
