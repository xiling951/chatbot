$ErrorActionPreference = 'Stop'
$taskData = Join-Path $env:LOCALAPPDATA 'CompanionCore\runtime\data'
$taskPath = Join-Path $taskData 'companion-process.local.json'
$taskStop = Join-Path $taskData 'plugin_data\companion_core\STOP'
New-Item -ItemType File -Path $taskStop -Force | Out-Null
if (Test-Path -LiteralPath $taskPath) {
    $taskSaved = Get-Content -LiteralPath $taskPath -Raw | ConvertFrom-Json
    $taskProcess = Get-Process -Id $taskSaved.id -ErrorAction SilentlyContinue
    if ($taskProcess -and $taskProcess.StartTime.ToUniversalTime().Ticks -eq $taskSaved.started_ticks) {
        $taskChildren = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($taskProcess.Id)"
        foreach ($taskChild in $taskChildren) { Stop-Process -Id $taskChild.ProcessId -ErrorAction SilentlyContinue }
        Stop-Process -Id $taskProcess.Id
        Write-Output 'Stopped this AstrBot instance. STOP remains set for real model calls.'
    }
}
