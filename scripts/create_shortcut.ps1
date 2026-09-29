# Creates desktop shortcuts: "Nova" (starts the assistant) and "Nova Brain" (opens the dashboard, starting Nova if needed).
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

# "Nova Brain" opens the dashboard, and starts Nova first if it isn't running
Remove-Item "$desktop\Nova Brain.url" -ErrorAction SilentlyContinue
$u = $ws.CreateShortcut("$desktop\Nova Brain.lnk")
$u.TargetPath = "$root\brain.bat"
$u.WorkingDirectory = $root
$u.IconLocation = "$root\assets\nova.ico"
$u.WindowStyle = 7
$u.Description = "Open Nova's 3D brain (starts Nova if needed)"
$u.Save()

Write-Host "Desktop shortcuts created: Nova, Nova Brain"
