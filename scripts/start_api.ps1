# Start the API for F5 in VS Code — or say that one is already running.
#
# Why a script and not a one-liner in tasks.json: the inline version embedded a
# Python one-liner containing single quotes, commas and `timeout=2` inside a
# JSON string inside a PowerShell -Command argument. VS Code re-quotes that
# before handing it to powershell.exe, the inner quoting did not survive, and
# PowerShell then parsed `timeout=2` as its own expression:
#
#     Ausdruck nach "," fehlt.  /  Unerwartetes Token "timeout=2"
#
# A script file has no such layer: tasks.json passes one path, and the quoting
# lives here where PowerShell is the only thing reading it.
#
# Behaviour: if something already answers on the port, reuse it rather than
# failing to bind and taking the browser launch down with it (the compound
# launch starts two Chrome windows against one API). Otherwise start uvicorn in
# the foreground, so the task's problemMatcher sees its startup lines.

param(
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'

function Test-ApiUp {
    param([int]$Port)
    try {
        # -UseBasicParsing keeps this working on Windows PowerShell 5.1 without IE.
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" `
                               -TimeoutSec 2 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

if (Test-ApiUp -Port $Port) {
    # The endsPattern in tasks.json watches for this phrasing, so the task
    # completes instead of hanging while a perfectly good API is already serving.
    Write-Host "Uvicorn running on http://127.0.0.1:$Port (reusing existing API)"
    exit 0
}

Write-Host "Starting API on port $Port ..."
python -m uvicorn api.server:app --port $Port
exit $LASTEXITCODE
