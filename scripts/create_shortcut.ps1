# Creates desktop shortcuts: "Nova" (starts the assistant) and "Nova Brain" (opens the dashboard).
$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$ws = New-Object -ComObject WScript.Shell

$s = $ws.CreateShortcut("$desktop\Nova.lnk")
$s.TargetPath = "$root\run.bat"
$s.WorkingDirectory = $root
$s.IconLocation = "$root\assets\nova.ico"
$s.WindowStyle = 7          # start minimised
$s.Description = "Start Nova, your voice assistant"
$s.Save()

$u = $ws.CreateShortcut("$desktop\Nova Brain.url")
$u.TargetPath = "http://localhost:8765"
$u.Save()
Add-Content "$desktop\Nova Brain.url" "IconFile=$root\assets\nova.ico`r`nIconIndex=0"

Write-Host "Desktop shortcuts created: Nova, Nova Brain"
