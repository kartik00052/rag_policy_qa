<#
.SYNOPSIS
    Start the backend dev server and wait until it is actually serving.

.DESCRIPTION
    The single correct way to bring the server up. It exists because getting
    this invocation right was repeatedly re-derived under time pressure and got
    it wrong twice:

      1. Bypassing run.py and calling uvicorn directly breaks psycopg async on
         Windows, because run.py is what installs the selector event loop.
      2. Doing `cd backend` and then `Start-Process ... run.py` does NOT work.
         Start-Process does not inherit the caller's working directory, so run.py
         was resolved relative to something else entirely and the process died
         before binding the port - with a bare error on stderr and no listener to
         notice.

    This script removes both failure modes:
      * -WorkingDirectory is always an absolute path derived from $PSScriptRoot,
        so it works no matter where the caller is.
      * The port is probed and /health is polled with a real timeout, and the
        server's stderr tail is printed automatically on failure, so a dead
        process reports itself instead of looking like a slow start.

    RELOAD=false is enforced because WORKFLOW.md documents that dev auto-reload
    cancels in-flight ingestion (ingestion runs in-process in V1), and it also
    changes uvicorn's event loop selection.

.PARAMETER Port
    Port to serve on. Default 8000.

.PARAMETER HealthTimeoutSeconds
    How long to wait for /health to return ok. Default 180, which covers a cold
    start on a slow machine.

.PARAMETER NoKill
    Do not stop a process already listening on the port. Fails instead, so it is
    never ambiguous which server answered a later request.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File backend\scripts\start_server.ps1
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [int]$HealthTimeoutSeconds = 180,
    [switch]$NoKill
)

$ErrorActionPreference = 'Stop'

# backend\scripts\start_server.ps1 -> backend\
$BackendDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$EnvFile = Join-Path $BackendDir '.env'
$LogDir = Join-Path $env:TEMP 'rag_policy_logs'
$StdOutLog = Join-Path $LogDir 'server.out.log'
$StdErrLog = Join-Path $LogDir 'server.err.log'

function Write-Stage([string]$Message) {
    Write-Host "==> $Message"
}

# --- 1. Preconditions -------------------------------------------------------

if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "No .env at $EnvFile. Copy .env.example and fill it in before starting."
}

# RELOAD=false is required for anything that runs work inside the process.
$reloadLine = Select-String -Path $EnvFile -Pattern '^\s*RELOAD\s*=' | Select-Object -Last 1
if (-not $reloadLine) {
    Write-Warning "RELOAD is not set in .env; defaulting to reload=true, which cancels in-flight ingestion."
} elseif ($reloadLine.Line -match '=\s*false\s*$') {
    Write-Stage "RELOAD=false confirmed in .env"
} else {
    throw ("RELOAD must be false for this workflow (found '{0}'). " +
           "Dev auto-reload cancels in-flight ingestion because V1 has no worker queue." -f $reloadLine.Line.Trim())
}

# --- 2. Clear the port ------------------------------------------------------

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    if ($NoKill) {
        throw "Port $Port is already in use by pid(s) $($listener.OwningProcess -join ', ') and -NoKill was given."
    }
    Write-Stage "Stopping existing listener(s) on port $Port"
    foreach ($proc in ($listener.OwningProcess | Select-Object -Unique)) {
        Write-Host "    killing pid $proc"
        Stop-Process -Id $proc -Force -ErrorAction SilentlyContinue
    }
    # Wait for the port to actually clear rather than assuming 2s is enough.
    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $Port is still occupied after killing the previous process."
    }
    Write-Stage "Port $Port is free"
} else {
    Write-Stage "Port $Port already free (cold start)"
}

# --- 3. Launch --------------------------------------------------------------

# Guard the port. run.py hardcodes the bind port, so a -Port that disagrees with
# it would make this script probe a port nothing is listening on - and, worse,
# skip clearing the port the new server is about to fight over. Fail loudly
# rather than let that happen under time pressure.
$runPy = Join-Path $BackendDir 'run.py'
$portLiteral = Select-String -Path $runPy -Pattern '^\s*port\s*=\s*(\d+)\s*,\s*$' |
    Select-Object -First 1
if (-not $portLiteral -or [int]$portLiteral.Matches[0].Groups[1].Value -ne $Port) {
    $actual = if ($portLiteral) { $portLiteral.Matches[0].Groups[1].Value } else { 'not found' }
    throw ("-Port $Port does not match the port run.py binds ($actual). " +
           "Change both together, or drop -Port and use the default.")
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
foreach ($log in @($StdOutLog, $StdErrLog)) {
    if (Test-Path -LiteralPath $log) { Remove-Item -LiteralPath $log -Force }
}

# Launch DETACHED, and this is deliberate rather than incidental.
#
# Start-Process only uses ShellExecute when there is no -Redirect*/-NoNewWindow.
# With a redirect it switches to CreateProcess(bInheritHandles=TRUE), which
# duplicates *every* inheritable handle of this process into the child - including
# the pipes the calling harness reads the command's output from. The server tree
# outlives this script by design, so it kept those copies open and the caller
# blocked on a pipe that would never reach EOF, even though the script itself
# exited 0. Observed as "start_server.ps1 printed READY and EXITCODE=0 but the
# tool call never returned".
#
# So: no -Redirect* here. ShellExecuteEx inherits no handles, the server gets its
# own hidden console, and cmd performs the log redirection inside the child. The
# log files, the /health poll and the exit detection all behave as before.
$commandLine = 'uv run python run.py 1>"{0}" 2>"{1}"' -f $StdOutLog, $StdErrLog
Write-Stage "Starting: $commandLine  (cwd: $BackendDir)"
$process = Start-Process -FilePath $env:ComSpec `
    -ArgumentList @('/c', $commandLine) `
    -WorkingDirectory $BackendDir `
    -WindowStyle Hidden `
    -PassThru

# --- 4. Poll /health --------------------------------------------------------

Write-Stage "Waiting up to $HealthTimeoutSeconds seconds for /health on http://127.0.0.1:$Port"
$waitStarted = Get-Date
$deadline = $waitStarted.AddSeconds($HealthTimeoutSeconds)
$healthy = $false
$lastError = $null
$nextProgressAt = 10

while ((Get-Date) -lt $deadline) {
    # Catch the case where run.py failed to start at all.
    if ($process.HasExited) {
        Write-Host ''
        Write-Host "FAILED: server process exited with code $($process.ExitCode) after $([int]((Get-Date) - $process.StartTime).TotalSeconds)s" -ForegroundColor Red
        Write-Host "--- $StdErrLog (tail) ---" -ForegroundColor Red
        if (Test-Path -LiteralPath $StdErrLog) { Get-Content -LiteralPath $StdErrLog -Tail 40 }
        Write-Host "--- $StdOutLog (tail) ---" -ForegroundColor Red
        if (Test-Path -LiteralPath $StdOutLog) { Get-Content -LiteralPath $StdOutLog -Tail 40 }
        exit 1
    }

    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch {
        $lastError = $_.Exception.Message
    }

    # A cold start legitimately takes tens of seconds, and a start that is
    # blocked (e.g. Postgres down, so the app never finishes startup and never
    # binds the port) is indistinguishable from a slow one unless this says so.
    # Without it the caller just sees a silent multi-minute block and concludes
    # the script has hung.
    $elapsed = [int]((Get-Date) - $waitStarted).TotalSeconds
    if ($elapsed -ge $nextProgressAt) {
        $left = [int]($deadline - (Get-Date)).TotalSeconds
        $why = if ($lastError) { $lastError } else { 'no response yet - server has not bound the port' }
        Write-Host "    still waiting: ${elapsed}s elapsed, ~${left}s left ($why)"
        $nextProgressAt = $elapsed + 10
    }

    Start-Sleep -Seconds 2
}

if (-not $healthy) {
    Write-Host ''
    Write-Host "FAILED: /health did not return ok within $HealthTimeoutSeconds seconds." -ForegroundColor Red
    if ($lastError) { Write-Host "last error: $lastError" }
    Write-Host "--- $StdErrLog (tail) ---" -ForegroundColor Red
    if (Test-Path -LiteralPath $StdErrLog) { Get-Content -LiteralPath $StdErrLog -Tail 40 }
    Write-Host "--- $StdOutLog (tail) ---" -ForegroundColor Red
    if (Test-Path -LiteralPath $StdOutLog) { Get-Content -LiteralPath $StdOutLog -Tail 40 }
    exit 1
}

# --- 5. Report --------------------------------------------------------------

$health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 15
# The launched pid is the cmd launcher, not the server, so report the pid that is
# actually holding the port - that is the one to kill, and the one to look for if
# the server dies later.
$servingPid = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1).OwningProcess
Write-Host ''
Write-Host "READY  launcher pid $($process.Id)  http://127.0.0.1:$Port" -ForegroundColor Green
if ($servingPid) { Write-Host "  serving pid $servingPid" }
Write-Host "  status: $($health.status)"
foreach ($dep in $health.dependencies) {
    $mark = if ($dep.ok) { 'ok  ' } else { 'FAIL' }
    $colour = if ($dep.ok) { 'Green' } else { 'Red' }
    Write-Host "  [$mark] $($dep.name): $($dep.detail) ($($dep.latency_ms)ms)" -ForegroundColor $colour
}
if ($health.dependencies | Where-Object { -not $_.ok }) {
    Write-Host ''
    Write-Host "Server is up but at least one dependency is unhealthy." -ForegroundColor Red
    exit 1
}
Write-Host "  logs: $StdOutLog / $StdErrLog"
exit 0
