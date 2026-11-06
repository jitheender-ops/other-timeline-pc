# The Other Timeline - safe, reversible tune-up for long video renders.
# Undo with optimize_pc_undo.ps1. Does not touch Defender, firewall, UAC, updates, privacy or services.
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Host "Please run this as administrator (INSTALL.bat does this for you)."; Read-Host "Press Enter to close"; exit 1 }
$added = Join-Path $PSScriptRoot "optimize_pc_added.txt"   # GPU entries we created, for the undo script

function Set-Reg($path, $name, $value, $type = "DWord") {
    if (-not (Test-Path $path)) { New-Item -Path $path -Force | Out-Null }
    New-ItemProperty -Path $path -Name $name -Value $value -PropertyType $type -Force | Out-Null
}

Write-Host "1. Power plan: fast mode, never sleep while plugged in..."
$list = powercfg /list | Out-String
$g = $null
if ($list -match '([0-9a-f-]{36})\s+\(Ultimate Performance\)') { $g = $Matches[1] }
if (-not $g -and ((powercfg -duplicatescheme e9a42b02-d5df-448d-aa00-03f14749eb61 2>$null | Out-String) -match '([0-9a-f-]{36})')) { $g = $Matches[1] }
if (-not $g -and $list -match '(8c5e7fda-e8bf-4a96-9a85-cf48e3d1f0d8)') { $g = $Matches[1] }
if (-not $g -and ((powercfg -duplicatescheme 8c5e7fda-e8bf-4a96-9a85-cf48e3d1f0d8 2>$null | Out-String) -match '([0-9a-f-]{36})')) { $g = $Matches[1] }
if ($g) {
    powercfg /setactive $g
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    powercfg /change disk-timeout-ac 0
    powercfg /change monitor-timeout-ac 15
    Write-Host "   Done. Screen still turns off after 15 minutes. Battery settings unchanged."
} else { Write-Host "   Could not find a high performance plan. Skipped." }

Write-Host "2. Making sure Windows manages the page file (needed with 8 GB of memory)..."
$cs = Get-CimInstance Win32_ComputerSystem
if ($cs.AutomaticManagedPagefile) { Write-Host "   Already fine." }
else { Set-CimInstance -InputObject $cs -Property @{AutomaticManagedPagefile = $true}; Write-Host "   Fixed. Takes effect after a restart." }

Write-Host "3. Visual effects: best performance, keeping smooth fonts..."
# VisualFXSetting alone only moves the radio button; UserPreferencesMask + MinAnimate are what turn effects off.
$desk = "HKCU:\Control Panel\Desktop"
$backup = Join-Path $PSScriptRoot "optimize_pc_mask.txt"   # original mask, restored by the undo script
if (-not (Test-Path $backup)) { ((Get-ItemProperty $desk).UserPreferencesMask | ForEach-Object { $_.ToString("X2") }) -join "," | Set-Content $backup }
Set-Reg "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects" "VisualFXSetting" 3
Set-Reg $desk "UserPreferencesMask" ([byte[]](0x90,0x12,0x03,0x80,0x10,0x00,0x00,0x00)) "Binary"
Set-Reg "$desk\WindowMetrics" "MinAnimate" "0" "String"
Set-Reg "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" "TaskbarAnimations" 0
Set-Reg $desk "FontSmoothing" "2" "String"
Set-Reg "HKCU:\Control Panel\Desktop" "FontSmoothingType" 2
Write-Host "   Done. Takes full effect after signing out and in."

Write-Host "4. Asking Windows to use the strong graphics card for Chrome, Node and FFmpeg..."
$ff = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
if (-not $ff) { $ff = "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe" }
if (Test-Path $ff) { $t = @((Get-Item $ff).Target)[0]; if ($t) { $ff = $t } }   # WinGet Links are shortcuts to the real exe
$apps = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
          "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe", "$env:ProgramFiles\nodejs\node.exe", $ff)
$gpu = "HKCU:\Software\Microsoft\DirectX\UserGpuPreferences"
if (-not (Test-Path $gpu)) { New-Item -Path $gpu -Force | Out-Null }
foreach ($a in $apps) {
    if (-not $a -or -not (Test-Path $a)) { continue }
    if ($null -eq (Get-ItemProperty -Path $gpu -Name $a -ErrorAction SilentlyContinue)) { Add-Content -Path $added -Value $a }
    Set-Reg $gpu $a "GpuPreference=2;" "String"
    Write-Host "   $a"
}

Write-Host "5. Turning off Xbox Game Bar background recording..."
Set-Reg "HKCU:\System\GameConfigStore" "GameDVR_Enabled" 0
Set-Reg "HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR" "AppCaptureEnabled" 0

Write-Host "6. Clearing temporary files older than 7 days..."
Get-ChildItem $env:TEMP -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host "7. This PC:"
Write-Host ("   Memory: {0:N0} GB" -f ($cs.TotalPhysicalMemory / 1GB))
$drive = Get-PSDrive -Name (Split-Path $PSScriptRoot -Qualifier).TrimEnd(':')
$free = [math]::Round($drive.Free / 1GB)
Write-Host "   Free space on drive $($drive.Name): $free GB"
if ($free -lt 40) { Write-Host "   WARNING: less than 40 GB free. Delete old videos or files to make room." -ForegroundColor Yellow }
Get-CimInstance Win32_VideoController | ForEach-Object { Write-Host "   Graphics: $($_.Name)" }

Write-Host ""
Write-Host "All done. To undo, right-click optimize_pc_undo.ps1 and choose Run with PowerShell."
Read-Host "Press Enter to close"
