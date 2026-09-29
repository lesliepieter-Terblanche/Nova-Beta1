# Start Nova automatically when you log in to Windows.
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 on
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 off
param([string]$mode = "on")
$root = Split-Path -Parent $PSScriptRoot
$link = Join-Path ([Environment]::GetFolderPath('Startup')) "Nova.lnk"
if ($mode -eq "off") { Remove-Item $link -ErrorAction SilentlyContinue; Write-Host "Autostart off"; exit }
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($link)
$s.TargetPath = "$root\run.bat"; $s.WorkingDirectory = $root; $s.WindowStyle = 7
$s.IconLocation = "$root\assets\nova.ico"; $s.Save()
Write-Host "Nova will start when you log in."
