# Removes what install_windows.ps1 added (shortcuts + scheduled task). Your data in data\ is NOT touched.
$ErrorActionPreference = 'SilentlyContinue'
foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Startup'))) {
    $path = Join-Path $folder 'Job Search Command Center.lnk'
    if (Test-Path $path) { Remove-Item $path -Force; Write-Host "removed: $path" }
}
Unregister-ScheduledTask -TaskName 'Job Search Command Center - wake and search' -Confirm:$false
Write-Host 'removed: scheduled task (if it existed). Your data folder is unchanged.'
