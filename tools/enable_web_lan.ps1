$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Right-click enable_web_lan.bat and select Run as administrator.'
}
$name = 'Kandu Web LAN (TCP 8766)'
if (-not (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol TCP -LocalPort 8766 -Action Allow -Profile Private -RemoteAddress LocalSubnet | Out-Null
}
Write-Host 'LAN access enabled on TCP 8766 for the private local subnet.'
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' } | ForEach-Object {
    Write-Host ('http://{0}:8766   ({1})' -f $_.IPAddress, $_.InterfaceAlias)
}
