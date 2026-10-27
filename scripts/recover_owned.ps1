$ErrorActionPreference = 'Stop'
$taskGuard = New-Object System.Threading.Mutex($false, 'Local\CompanionCoreLifecycle')
$taskHeld = $false
try {
try { $taskHeld = $taskGuard.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $taskHeld = $true }
if (-not $taskHeld) { exit 0 }
$taskRootConfig = Join-Path $PSScriptRoot '..\config\runtime_root.local.json'
$taskRuntimeRoot = if (Test-Path -LiteralPath $taskRootConfig) { (Get-Content -LiteralPath $taskRootConfig -Raw -Encoding UTF8 | ConvertFrom-Json).runtime_root } else { Join-Path $env:LOCALAPPDATA 'CompanionCore\runtime' }
$taskData = Join-Path $taskRuntimeRoot 'data'
$taskStop = Join-Path $taskData 'plugin_data\companion_core\STOP'
$taskProcessFile = Join-Path $taskData 'companion-process.local.json'
if (Test-Path -LiteralPath $taskStop) { exit 0 }
if (Test-Path -LiteralPath $taskProcessFile) {
    $taskSaved = Get-Content -LiteralPath $taskProcessFile -Raw | ConvertFrom-Json
    $taskProcess = Get-Process -Id $taskSaved.id -ErrorAction SilentlyContinue
    if ($taskProcess -and $taskProcess.StartTime.ToUniversalTime().Ticks -eq $taskSaved.started_ticks) {
        $taskChildren = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($taskProcess.Id)"
        foreach ($taskChild in $taskChildren) { Stop-Process -Id $taskChild.ProcessId -ErrorAction SilentlyContinue }
        Stop-Process -Id $taskProcess.Id -ErrorAction SilentlyContinue
    }
}
if (-not (Test-Path -LiteralPath $taskStop)) { & (Join-Path $PSScriptRoot 'start.ps1') }
} finally {
    if ($taskHeld) { $taskGuard.ReleaseMutex() }
    $taskGuard.Dispose()
}
