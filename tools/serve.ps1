<#
    Bring up the judged surface: uvicorn behind a cloudflared quick tunnel.

    The tunnel hostname is created with the process and dies with it, and a
    restart yields a different one that nobody has (CLAUDE.md section 6). So this
    script refuses to print a URL it has not itself verified end to end -- a URL
    submitted on trust is the one failure with no recovery.

        .\tools\serve.ps1           bring it up and print the URL
        .\tools\serve.ps1 -Stop     shut both down
        .\tools\serve.ps1 -Check    verify what is already running
#>
[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$Check,
    [int]$Port = 8123
)

$ErrorActionPreference = "Stop"
$repo    = Split-Path -Parent $PSScriptRoot
$logDir  = Join-Path $repo "logs"
$appLog  = Join-Path $logDir "uvicorn.log"
$tunLog  = Join-Path $logDir "cloudflared.log"
$urlFile = Join-Path $logDir "public-url.txt"
$local   = "http://127.0.0.1:$Port"

# PATH is not dependable here: a PATH edit never reaches an already-open shell,
# and launch day is the wrong time to discover that. Fall back to the known
# install spot before giving up.
function Resolve-Cloudflared {
    $onPath = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    foreach ($candidate in @(
        (Join-Path $env:USERPROFILE "bin\cloudflared.exe"),
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\cloudflared.exe"),
        "C:\Program Files (x86)\cloudflared\cloudflared.exe"
    )) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Say($text, $colour = "Gray") { Write-Host $text -ForegroundColor $colour }
function Ok($text)   { Say "  [ok]   $text" "Green" }
function Bad($text)  { Say "  [FAIL] $text" "Red" }
function Info($text) { Say "  [..]   $text" "DarkGray" }

function Get-Listener {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conn) { return Get-Process -Id ($conn | Select-Object -First 1).OwningProcess -ErrorAction SilentlyContinue }
    return $null
}

# cloudflared banners the URL on stderr, so both streams have to be searched.
function Read-TunnelUrl {
    $paths = @($tunLog, "$tunLog.err") | Where-Object { Test-Path $_ }
    if (-not $paths) { return $null }
    $hit = Select-String -Path $paths -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -AllMatches |
           Select-Object -First 1
    if ($hit) { return $hit.Matches[0].Value }
    return $null
}

# Probe healthz and return the parsed body, or $null. Never throws.
function Test-Healthz($base, $timeoutSec = 15) {
    try {
        $r = Invoke-RestMethod -Uri "$base/v1/healthz" -TimeoutSec $timeoutSec
        if ($r.status -eq "ok") { return $r }
        return $null
    } catch { return $null }
}

# ----------------------------------------------------------------- stop

if ($Stop) {
    Say "`nStopping." "Cyan"
    $listener = Get-Listener
    if ($listener) { Stop-Process -Id $listener.Id -Force; Ok "uvicorn (pid $($listener.Id))" }
    else { Info "nothing listening on $Port" }

    $tun = Get-Process cloudflared -ErrorAction SilentlyContinue
    if ($tun) { $tun | Stop-Process -Force; Ok "cloudflared ($($tun.Count) process)" }
    else { Info "cloudflared not running" }

    if (Test-Path $urlFile) { Remove-Item $urlFile -Force }
    Say ""
    return
}

# ----------------------------------------------------------------- check

if ($Check) {
    Say "`nChecking what is up." "Cyan"
    $listener = Get-Listener
    if ($listener) { Ok "uvicorn listening on $Port (pid $($listener.Id))" } else { Bad "nothing on $Port" }

    $h = Test-Healthz $local 8
    if ($h) {
        Ok "local healthz -- uptime $($h.uptime_seconds)s"
        $c = $h.contexts_loaded
        Info "contexts: category=$($c.category) merchant=$($c.merchant) customer=$($c.customer) trigger=$($c.trigger)"
    } else { Bad "local healthz did not answer" }

    $url = Read-TunnelUrl
    if ($url) {
        $t = Test-Healthz $url 15
        if ($t) { Ok "public healthz -- $url (uptime $($t.uptime_seconds)s)" }
        else { Bad "tunnel URL $url is not answering" }
    } else { Bad "no tunnel URL in $tunLog" }
    Say ""
    return
}

# ----------------------------------------------------------------- preflight

Say "`nPreflight." "Cyan"

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$existing = Get-Listener
if ($existing) {
    Bad "port $Port is already held by $($existing.ProcessName) (pid $($existing.Id))"
    Say "        Run '.\tools\serve.ps1 -Stop' first. Starting a second instance would" "DarkGray"
    Say "        split the in-memory context store and fail warmup." "DarkGray"
    exit 1
}
Ok "port $Port is free"

$cloudflared = Resolve-Cloudflared
if (-not $cloudflared) {
    Bad "cloudflared not found on PATH or in the usual install locations"
    exit 1
}
Ok "cloudflared at $cloudflared"

$envFile = Join-Path $repo ".env"
if ((Test-Path $envFile) -and (Select-String -Path $envFile -Pattern "GEMINI_API_KEY" -Quiet)) {
    Ok "GEMINI_API_KEY present in .env"
} else {
    Say "  [warn] no GEMINI_API_KEY in .env -- the bot will serve templates only" "Yellow"
}

$stale = Get-Process cloudflared -ErrorAction SilentlyContinue
if ($stale) {
    Say "  [warn] $($stale.Count) cloudflared process already running; stopping it" "Yellow"
    $stale | Stop-Process -Force
    Start-Sleep -Seconds 2
}

# ----------------------------------------------------------------- bring up

Say "`nStarting." "Cyan"

Remove-Item $appLog, "$appLog.err", $tunLog, "$tunLog.err", $urlFile -Force -ErrorAction SilentlyContinue

$env:PYTHONIOENCODING = "utf-8"
Start-Process -FilePath "python" `
    -ArgumentList "-m", "uvicorn", "vera.app:app", "--host", "127.0.0.1", "--port", "$Port", "--workers", "1" `
    -WorkingDirectory $repo -RedirectStandardOutput $appLog -RedirectStandardError "$appLog.err" `
    -WindowStyle Hidden | Out-Null

$up = $null
foreach ($i in 1..20) {
    Start-Sleep -Milliseconds 750
    $up = Test-Healthz $local 5
    if ($up) { break }
}
if (-not $up) {
    Bad "uvicorn did not answer healthz. Last lines of $appLog.err:"
    if (Test-Path "$appLog.err") { Get-Content "$appLog.err" -Tail 15 | ForEach-Object { Say "         $_" "DarkGray" } }
    exit 1
}
Ok "uvicorn answering locally"

Start-Process -FilePath $cloudflared `
    -ArgumentList "tunnel", "--url", $local, "--no-autoupdate" `
    -WorkingDirectory $repo -RedirectStandardOutput $tunLog -RedirectStandardError "$tunLog.err" `
    -WindowStyle Hidden | Out-Null

$url = $null
foreach ($i in 1..30) {
    Start-Sleep -Seconds 1
    $url = Read-TunnelUrl
    if ($url) { break }
}
if (-not $url) {
    Bad "cloudflared never printed a URL. Last lines of ${tunLog}:"
    foreach ($f in @($tunLog, "$tunLog.err")) {
        if (Test-Path $f) { Get-Content $f -Tail 10 | ForEach-Object { Say "         $_" "DarkGray" } }
    }
    exit 1
}
Ok "tunnel is up"

# ----------------------------------------------------------------- verify

Say "`nVerifying through the public URL." "Cyan"

$health = $null
foreach ($i in 1..15) {
    $health = Test-Healthz $url 15
    if ($health) { break }
    Start-Sleep -Seconds 2
}
if (-not $health) {
    Bad "public healthz never answered -- NOT submitting this URL"
    exit 1
}
Ok "healthz answers publicly (uptime $($health.uptime_seconds)s)"

try {
    $meta = Invoke-RestMethod -Uri "$url/v1/metadata" -TimeoutSec 15
    if ($meta.team_name) { Ok "metadata answers -- team '$($meta.team_name)', model '$($meta.model)'" }
    else { Bad "metadata answered without a team_name"; exit 1 }
} catch { Bad "metadata did not answer: $($_.Exception.Message)"; exit 1 }

# The judge polls healthz every 60s and three consecutive failures disqualifies
# the slot, so a single success is not evidence of a stable tunnel.
Info "sampling healthz 5x to confirm the tunnel is steady..."
$fails = 0
foreach ($i in 1..5) {
    if (-not (Test-Healthz $url 10)) { $fails++ }
    Start-Sleep -Seconds 2
}
if ($fails -gt 0) {
    Bad "$fails of 5 public healthz probes failed -- do not submit this URL"
    exit 1
}
Ok "5 of 5 healthz probes clean"

$url | Set-Content -Path $urlFile -Encoding utf8

Say ""
Say "  ============================================================" "Green"
Say "   SUBMIT THIS URL" "Green"
Say "   $url" "White"
Say "  ============================================================" "Green"
Say ""
Say "  Verified: healthz, metadata, 5 consecutive public probes." "DarkGray"
Say "  Saved to: $urlFile" "DarkGray"
Say ""
Say "  From here until the score report lands (~105 min: warmup at T-15," "Yellow"
Say "  report at T0+90) do not restart either process. A cloudflared" "Yellow"
Say "  restart issues a DIFFERENT hostname and the submitted one dies." "Yellow"
Say "  The judge polls healthz every 60s; 3 consecutive misses is a" "Yellow"
Say "  disqualification, so the downtime budget is under 3 minutes." "Yellow"
Say ""
Say "  Before the window: power plan set to never sleep, Windows Update" "DarkGray"
Say "  paused, ethernet if available, nothing else launched." "DarkGray"
Say ""
Say "  Status later:  .\tools\serve.ps1 -Check" "DarkGray"
Say "  Shut down:     .\tools\serve.ps1 -Stop" "DarkGray"
Say ""
