# Undo optimize_pc.ps1: Balanced power plan, normal visual effects, remove our GPU entries, Game Bar recording back on.
Write-Host "1. Power plan back to Balanced..."
powercfg /setactive 381b4222-f694-41f0-9685-ff5bb260df2e

Write-Host "2. Visual effects back to 'let Windows choose'..."
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects" -Name "VisualFXSetting" -Value 0 -ErrorAction SilentlyContinue

Write-Host "3. Removing the graphics card choices we added..."
$added = Join-Path $PSScriptRoot "optimize_pc_added.txt"
if (Test-Path $added) {
    Get-Content $added | Where-Object { $_ } | ForEach-Object {
        Remove-ItemProperty -Path "HKCU:\Software\Microsoft\DirectX\UserGpuPreferences" -Name $_ -ErrorAction SilentlyContinue
        Write-Host "   $_"
    }
    Remove-Item $added
}

Write-Host "4. Xbox Game Bar recording back on..."
Set-ItemProperty -Path "HKCU:\System\GameConfigStore" -Name "GameDVR_Enabled" -Value 1 -ErrorAction SilentlyContinue
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR" -Name "AppCaptureEnabled" -Value 1 -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Undo finished. Sign out and back in for the visual effects to return."
Read-Host "Press Enter to close"
