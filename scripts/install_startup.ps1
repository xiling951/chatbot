$ErrorActionPreference = 'Stop'
$taskName = 'CompanionCore_Runtime'
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '..\config\runtime_root.local.json'))) { throw 'Configure the resolved private runtime path before registering startup.' }
$taskPython = 'F:\chatbot-runtime\AstrBot\.venv\Scripts\python.exe'
$taskLegacyPython = 'F:\chatbot-runtime\AstrBot\.venv\Scripts\pythonw.exe'
$taskWatchdog = Join-Path $PSScriptRoot 'runtime_watchdog.py'
$taskWrapper = Join-Path $PSScriptRoot 'supervise_runtime.ps1'
$taskPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$taskArguments = "-NoProfile -WindowStyle Hidden -File `"$taskWrapper`""
if (-not (Test-Path -LiteralPath $taskPython) -or -not (Test-Path -LiteralPath $taskWatchdog)) { throw 'The tested runtime and watchdog must exist before registration.' }
$taskOld = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$taskOldFormat = $taskOld -and $taskOld.Actions.Execute -eq $taskLegacyPython -and $taskOld.Actions.Arguments -eq "`"$taskWatchdog`""
$taskNewFormat = $taskOld -and $taskOld.Actions.Execute -eq $taskPowerShell -and $taskOld.Actions.Arguments -eq $taskArguments
if ($taskOld -and -not ($taskOldFormat -or $taskNewFormat)) { throw 'A different task already uses the chosen name; no task was replaced.' }
$taskUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskAction = New-ScheduledTaskAction -Execute $taskPowerShell -Argument $taskArguments -WorkingDirectory $PSScriptRoot
$taskTrigger = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -Hidden -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $taskTrigger -Principal $taskPrincipal -Settings $taskSettings -Description 'Owner-only Companion Core runtime recovery; respects STOP and sends no test messages.' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output 'Current-user hidden logon recovery task registered and started. Computer sleep and network settings were not changed.'
