# start-debug-browser.ps1
# Launches a Chromium browser (Edge first on this machine, Chrome as fallback) with remote debugging
# ENABLED on a SEPARATE profile, so the zju-literature-downloader-skill workflow can drive it over CDP.
# A separate profile is required because current Chrome/Edge builds ignore --remote-debugging-port
# for the default profile. Sign in to ZJU Library / WebVPN once inside this window.
#
# -Proxy http://127.0.0.1:8888 : route ALL browser traffic through a local proxy tunnel
#   (e.g. `ssh -N -L 8888:127.0.0.1:8888 aliyun` to the zju-connect aTrust VPN, see the
#   zju-atrust-vpn skill). Use this OFF campus when Summon/publisher says the IP is not
#   authorized; on campus or when using WebVPN pages, omit it.
param(
  [int]$Port = 9222,
  [string]$Browser = "",        # "edge" | "chrome" | "" (auto: Edge first on this machine)
  [string]$ProfileDir = "",     # default: per-browser dir under %LOCALAPPDATA%
  [string]$Proxy = "",          # optional, e.g. http://127.0.0.1:8888 (campus VPN tunnel)
  [switch]$NoOpen
)
$ErrorActionPreference = "Stop"

$edgeCandidates = @(
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe"
)
$chromeCandidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)

$kind = $Browser.ToLower()
if ($kind -notin @("edge", "chrome")) {
  $edge = $edgeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
  $kind = if ($edge) { "edge" } else { "chrome" }   # this machine prefers Edge
}
if ($kind -eq "edge") {
  $exe = $edgeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
} else {
  $exe = $chromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $exe) { throw "No Edge or Chrome executable found (Browser=$Browser)." }

if (-not $ProfileDir) { $ProfileDir = "$env:LOCALAPPDATA\zju-debug-browser-$kind" }
New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null

$args = @(
  "--remote-debugging-port=$Port",
  "--user-data-dir=$ProfileDir",
  "--no-first-run",
  "--no-default-browser-check",
  "--disable-features=Translate"
)
if ($Proxy) { $args += "--proxy-server=$Proxy" }
if (-not $NoOpen) {
  $args += "https://libweb.zju.edu.cn/"
  $args += "https://webvpn.zju.edu.cn/"
  $args += "https://zju.summon.serialssolutions.com/"
}

Start-Process -FilePath $exe -ArgumentList $args
Start-Sleep -Seconds 3
$exe | Set-Content -Encoding ascii "$PSScriptRoot\.browser-exe.txt"
Write-Output "launched: $exe"
Write-Output "profile:  $ProfileDir"
Write-Output "debug:    http://127.0.0.1:$Port/json/version"
if ($Proxy) { Write-Output "proxy:    $Proxy" }
try {
  $v = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 5
  Write-Output "CDP OK: $($v.Browser)"
} catch {
  Write-Output "CDP not answering yet: $($_.Exception.Message)"
}
