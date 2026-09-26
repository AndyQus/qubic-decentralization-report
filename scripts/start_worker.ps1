# Start the ingest worker for F5 in VS Code — the half that fills the store.
#
# Why this exists: the API serves what it finds and never backfills. Without a
# worker the dashboard shows a store frozen at whenever one last ran, which
# looks exactly like a broken page — a price "last confirmed 4.6 h ago", and a
# 503 on /v1/price/epochs because no epoch boundary was ever observed. Both are
# the store being empty, not the code being wrong. In the container the two run
# together (QDR_ROLE=both); locally F5 started only the API, so this restores
# the pairing.
#
# It runs scripts/worker.sh — the same script the image runs — rather than
# calling ingest.py directly, so what F5 exercises is the live startup path:
# backfill, refresh-stale, burn seed, then the watch loop. A second entrypoint
# would drift from it.
#
# That needs a POSIX shell. Git Bash ships with Git for Windows and is what we
# look for; `sh` on PATH is accepted too. Nothing else is: cmd and PowerShell
# cannot run worker.sh's mkfifo/tee logging at all.

param(
    [switch]$NoBurnBackfill = $true,
    [switch]$NoLiveSync
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot

# Refuse to be the second worker, the way start_api.ps1 refuses to be the second
# API — but for a different reason, and it matters more here. A second uvicorn
# merely fails to bind a port. A second worker starts cleanly and then competes
# for the same SQLite file: WAL mode is built for one writer, so the loser takes
# "database is locked" on a balance snapshot, and an epoch boundary missed that
# way is missed for good. It would also double every call to a Bob node that
# belongs to someone else.
#
# A leftover from a previous F5 is the normal case: the watch loop runs forever,
# so closing the browser does not end it.
$lock = Join-Path $repo "data\worker.lock"
New-Item -ItemType Directory -Force -Path (Split-Path $lock) | Out-Null
if (Test-Path $lock) {
    $old = Get-Content $lock -ErrorAction SilentlyContinue | Select-Object -First 1
    $alive = $null
    if ($old) { $alive = Get-Process -Id ([int]$old) -ErrorAction SilentlyContinue }
    if ($alive) {
        # tasks.json watches for "[worker] watching", so say it here too — the
        # task has to complete or the browser launch waits on it forever.
        Write-Host "[worker] watching (reusing the worker already running, PID $old)"
        exit 0
    }
    # Stale: the process is gone, so the file is just litter from a kill.
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
$PID | Set-Content $lock -Encoding ASCII

function Find-Bash {
    # Git for Windows FIRST, PATH second — the opposite of the obvious order,
    # and the reason is C:\Windows\System32\bash.exe. That is the WSL launcher,
    # it is on PATH on every modern Windows, and `Get-Command bash.exe` returns
    # it before any Git install. On a machine with no WSL distro it fails with
    #
    #     WSL ERROR: CreateProcessCommon:559: execvpe(/bin/bash) failed
    #
    # which says nothing about what actually went wrong. Even with a distro it
    # would be the wrong shell: worker.sh would run inside WSL, where the repo
    # path, the Python install and the venv are all somewhere else.
    #
    # Read "Program Files (x86)" via Get-Item, not "${env:ProgramFiles(x86)}":
    # parentheses inside a ${...} variable name are a parse error in Windows
    # PowerShell 5.1, and it fails at load time — the script never runs, and the
    # message points at a brace several lines further down.
    $pf86 = (Get-Item "Env:ProgramFiles(x86)" -ErrorAction SilentlyContinue).Value
    $candidates = @(
        "$env:ProgramFiles\Git\bin\bash.exe",
        "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
    )
    if ($pf86) { $candidates += "$pf86\Git\bin\bash.exe" }
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }

    # Only now PATH, and never the System32 stub.
    $onPath = Get-Command bash.exe -ErrorAction SilentlyContinue
    if ($onPath -and $onPath.Source -notlike "$env:SystemRoot\System32\*") {
        return $onPath.Source
    }
    return $null
}

$bash = Find-Bash
if (-not $bash) {
    Write-Host "[worker] No POSIX shell found — cannot run scripts/worker.sh."
    Write-Host "[worker] Install Git for Windows (it ships Git Bash), or run the"
    Write-Host "[worker] watch loop directly:"
    Write-Host "[worker]   python scripts/ingest.py --watch --interval 300 --export-each"
    exit 1
}

# The store: no DATA_DIR, so qdr.store falls back to the repo's own data/qdr.db —
# the same file the API reads. Setting DATA_DIR here would point the worker at a
# different database than the one the dashboard serves, which is the one failure
# mode that looks identical to "the worker is not running".
#
# The log does need a path, because worker.sh defaults it to ${DATA_DIR:-/data},
# and /data does not exist on Windows. Git Bash would create a stray folder at
# the drive root before falling back.
$env:QDR_LOG_FILE = Join-Path $repo "data\worker.log"

# Burn backfill: ~9 minutes per day of chain against a public Bob node that
# belongs to someone else. The container pays that once per deployment because
# its burn page would otherwise never have history. A dev box restarts far more
# often than a deployment does, so the default here is off — the live burn scan
# in the watch loop still runs, and still fills the running day.
if ($NoBurnBackfill) {
    $env:QDR_BURN_BACKFILL_DAYS = "0"
    Write-Host "[worker] burn backfill disabled locally (-NoBurnBackfill:`$false to enable)"
}

# Backfill fewer epochs than the container's 10: locally the point is a working
# page within a minute, not a full archive, and each epoch costs real calls.
if (-not $env:QDR_BACKFILL_EPOCHS) { $env:QDR_BACKFILL_EPOCHS = "2" }

# Fill the hours this machine was off from the live deployment: price and mining
# readings describe a moment and cannot be fetched afterwards, but live was
# watching. Runs only here, after the lock -- never beside a running worker,
# whose open price interval the copy would replace -- and never fails the
# launch: offline, it warns and the worker starts as before.
# -NoLiveSync skips it, for testing what the local worker alone produces.
if (-not $NoLiveSync) {
    Push-Location $repo
    try { python scripts/sync_from_live.py --days 7 } finally { Pop-Location }
}

Write-Host "Starting ingest worker (worker.sh via $bash) ..."

# Forward slashes and a relative path: MSYS rewrites Windows-style arguments on
# the way into bash, and a rewritten path here would miss the script.
Push-Location $repo
try {
    & $bash "scripts/worker.sh"
    exit $LASTEXITCODE
} finally {
    Pop-Location
    # Only ever remove our own lock. A hard kill (Stop-Process, or closing the
    # terminal) skips this entirely, which is why the check above validates the
    # PID rather than trusting that the file means a live worker.
    if ((Test-Path $lock) -and
        ((Get-Content $lock -ErrorAction SilentlyContinue | Select-Object -First 1) -eq "$PID")) {
        Remove-Item $lock -Force -ErrorAction SilentlyContinue
    }
}
