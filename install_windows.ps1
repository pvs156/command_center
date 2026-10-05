# Installs the Job Search Command Center as a background app for the current Windows user.
#   - Desktop + Start menu shortcut ("Job Search Command Center")
#   - Starts quietly at login (tray icon, no window)
#   - Scheduled task: every 2 hours, wake the PC (only when plugged in), search and tailor, then let it sleep
# Undo with uninstall_windows.ps1. No admin rights needed.
param([switch]$NoWake)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'

if (-not (Test-Path $python)) {
    Write-Host 'Creating the Python environment (first run, about a minute)...'
    python -m venv (Join-Path $root '.venv')
    & $python -m pip install -q -r (Join-Path $root 'requirements.txt')
}
& $python -m app.make_icon | Out-Null
$icon = Join-Path $root 'app\static\icon.ico'

$shell = New-Object -ComObject WScript.Shell
function New-AppShortcut([string]$folder, [string]$arguments) {
    $path = Join-Path $folder 'Job Search Command Center.lnk'
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $pythonw
    $link.Arguments = $arguments
    $link.WorkingDirectory = $root
    $link.IconLocation = $icon
    $link.Description = 'Job Search Command Center'
    $link.Save()
    Write-Host "  shortcut: $path"
}
Write-Host 'Creating shortcuts...'
New-AppShortcut ([Environment]::GetFolderPath('Desktop')) '-m app.tray'
New-AppShortcut ([Environment]::GetFolderPath('Programs')) '-m app.tray'
New-AppShortcut ([Environment]::GetFolderPath('Startup')) '-m app.tray --no-browser'

if (-not $NoWake) {
    Write-Host 'Registering the wake-and-search task (every 2 hours, plugged in only)...'
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument '-m app.wake' -WorkingDirectory $root
    $start = (Get-Date).Date.AddHours([math]::Ceiling((Get-Date).Hour / 2) * 2)
    $trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Hours 2)
    $settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    # Default task settings already skip the run on battery, so the laptop is only woken when plugged in.
    Register-ScheduledTask -TaskName 'Job Search Command Center - wake and search' -Action $action -Trigger $trigger `
        -Settings $settings -Description 'Wakes the PC every 2 hours (plugged in only), searches for new jobs and tailors resumes.' -Force | Out-Null
    Write-Host '  task: Job Search Command Center - wake and search'
}
Write-Host ''
Write-Host 'Done. Open "Job Search Command Center" from the desktop or Start menu. It also starts by itself when you log in.'
