# Allow phones on the same Wi-Fi to reach Beanthentic Client Web on this laptop.
#
# If you get "running scripts is disabled", use either:
#   scripts\allow-lan-access.bat   (right-click -> Run as administrator)
# or:
#   powershell -ExecutionPolicy Bypass -File .\scripts\allow-lan-access.ps1
#
# Right-click PowerShell -> Run as administrator, then:
#   cd "path\to\Beanthentic-Client-Web"
#   .\scripts\allow-lan-access.ps1

#Requires -RunAsAdministrator

$Port = 5001
if ($env:BEANTHENTIC_PORT) {
    $Port = [int]$env:BEANTHENTIC_PORT
}

$RuleName = "Beanthentic Client Web TCP $Port"
$existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue

if ($existing) {
    Write-Host "Firewall rule already exists: $RuleName"
}
else {
    New-NetFirewallRule `
        -DisplayName $RuleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -Profile Any | Out-Null
    Write-Host "Created firewall rule: allow inbound TCP port $Port"
}

Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. python web.py"
Write-Host "  2. On your phone, open the http://192.168.x.x:$Port/ URL printed in the terminal"
Write-Host "  3. Phone and laptop must use the same Wi-Fi (turn off mobile data on the phone)"
