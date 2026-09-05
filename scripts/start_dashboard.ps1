param(
    [string]$BrokerTerminal = 'C:\Program Files\MetaTrader 5\terminal64.exe',
    [ValidateNotNullOrEmpty()]
    [ValidatePattern('^[^\x00-\x1F"]+$')]
    [string]$Gate1Report = 'build/gate1/30day/report.json',
    [ValidateRange(15, 600)]
    [int]$StartupTimeoutSeconds = 300
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtimeRoot = Join-Path $repoRoot 'build/monitoring'
$pythonPath = Join-Path $runtimeRoot 'venv/Scripts/python.exe'
$grafanaRoot = Join-Path $runtimeRoot 'grafana'
$grafanaPath = Join-Path $grafanaRoot 'bin/grafana.exe'
$prometheusPath = Join-Path $runtimeRoot 'prometheus/prometheus.exe'
$statePath = Join-Path $runtimeRoot 'services.json'

foreach ($binary in @($pythonPath, $grafanaPath, $prometheusPath)) {
    if (-not (Test-Path -LiteralPath $binary)) {
        throw "Missing local runtime: $binary. Follow docs/runbooks/local-dashboard.md."
    }
}
foreach ($directory in @('logs', 'grafana-data', 'grafana-plugins', 'prometheus-data')) {
    New-Item -ItemType Directory -Path (Join-Path $runtimeRoot $directory) -Force | Out-Null
}

function Get-OwnedService($entry) {
    if ($null -eq $entry) { return $null }
    $existing = Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
    if ($null -eq $existing) { return $null }
    # PowerShell 7 may deserialize ISO JSON strings as DateTime, whereas 5.1
    # leaves strings. Compare UTC ticks, not culture-dependent string coercion.
    $savedStart = if ($entry.started_at -is [datetime]) { $entry.started_at.ToUniversalTime() } else {
        [datetimeoffset]::Parse([string]$entry.started_at, [Globalization.CultureInfo]::InvariantCulture).UtcDateTime
    }
    if ($existing.Path -ne $entry.executable -or
        $existing.StartTime.ToUniversalTime().Ticks -ne $savedStart.Ticks) {
        throw "Saved PID for $($entry.name) belongs to a different process."
    }
    return $existing
}

function Save-ServiceState($entries) {
    $temporary = Join-Path $runtimeRoot ('services-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        $payload = @{services=@($entries.Values | Sort-Object name); dashboard='http://localhost:3000/d/tradebot-system'} |
            ConvertTo-Json -Depth 6
        [IO.File]::WriteAllText($temporary, $payload, (New-Object Text.UTF8Encoding($false)))
        if (Test-Path -LiteralPath $statePath) {
            [IO.File]::Replace($temporary, $statePath, [NullString]::Value)
        } else {
            [IO.File]::Move($temporary, $statePath)
        }
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
    }
}

function Update-ExporterChildren($entry) {
    $module = switch ($entry.name) {
        'exporter' { 'tradebot.monitoring.acquisition_exporter' }
        'broker' { 'tradebot.monitoring.broker_exporter' }
        default { return }
    }
    $modulePattern = '(?:^|\s)-m\s+' + [regex]::Escape($module) + '(?:\s|$)'
    $children = @{}
    foreach ($saved in @($entry.children)) {
        if ($null -ne $saved -and $null -ne (Get-OwnedService $saved)) {
            $children[[int]$saved.pid] = $saved
        }
    }
    $parent = Get-OwnedService $entry
    if ($null -ne $parent) {
        $queue = New-Object 'Collections.Generic.Queue[int]'
        $queue.Enqueue($parent.Id)
        while ($queue.Count -gt 0) {
            $parentId = $queue.Dequeue()
            $candidates = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $parentId")
            foreach ($candidate in $candidates) {
                # The venv launcher can own a base-Python worker. Its module identity,
                # parent relationship and start time distinguish it from the MT5 probe.
                if ($candidate.Name -notin @('python.exe', 'pythonw.exe') -or
                    $candidate.CommandLine -notmatch $modulePattern) {
                    continue
                }
                $worker = Get-Process -Id $candidate.ProcessId -ErrorAction SilentlyContinue
                if ($null -eq $worker -or $worker.StartTime -lt $parent.StartTime) { continue }
                if (-not $children.ContainsKey($worker.Id)) {
                    if ($children.Count -ge 32) { throw 'Unexpected exporter worker tree size.' }
                    $children[$worker.Id] = [ordered]@{name=($entry.name + '-worker'); pid=$worker.Id;
                        executable=$worker.Path; started_at=$worker.StartTime.ToUniversalTime().ToString('o')}
                    $queue.Enqueue($worker.Id)
                }
            }
        }
    }
    $entry.children = @($children.Values | Sort-Object started_at)
}
$env:PYTHONPATH = Join-Path $repoRoot 'src'
$env:PYTHONTZPATH = ''
$env:GF_PATHS_DATA = Join-Path $runtimeRoot 'grafana-data'
$env:GF_PATHS_LOGS = Join-Path $runtimeRoot 'logs'
$env:GF_PATHS_PLUGINS = Join-Path $runtimeRoot 'grafana-plugins'
$env:GF_PATHS_PROVISIONING = Join-Path $repoRoot 'deploy/grafana/provisioning'
$env:TRADEBOT_DASHBOARD_DIR = Join-Path $repoRoot 'deploy/grafana/dashboards'

$definitions = @(
    @{name='exporter'; executable=$pythonPath; port=8765; health='http://127.0.0.1:8765/health'; arguments=@(
        '-m', 'tradebot.monitoring.acquisition_exporter',
        '--plan', 'configs/probes/fbs_tick_continuity_v1.json',
        '--work-dir', 'build/fbs-tick-continuity-v1',
        '--report', 'docs/reports/fbs-tick-continuity-v1-candidate.json',
        '--gate1-report', ('"' + $Gate1Report + '"'),
        '--host', '127.0.0.1', '--port', '8765')},
    @{name='broker'; executable=$pythonPath; port=8766; health='http://127.0.0.1:8766/health'; arguments=@(
        '-m', 'tradebot.monitoring.broker_exporter',
        '--terminal', ('"' + $BrokerTerminal + '"'),
        '--host', '127.0.0.1', '--port', '8766')},
    @{name='prometheus'; executable=$prometheusPath; port=9090; health='http://127.0.0.1:9090/-/ready'; arguments=@(
        ('--config.file="' + (Join-Path $repoRoot 'deploy/prometheus/prometheus.yml') + '"'),
        ('--storage.tsdb.path="' + (Join-Path $runtimeRoot 'prometheus-data') + '"'),
        '--storage.tsdb.retention.time=30d', '--web.listen-address=127.0.0.1:9090')},
    @{name='grafana'; executable=$grafanaPath; port=3000; health='http://127.0.0.1:3000/api/health'; arguments=@(
        'server', ('--homepath="' + $grafanaRoot + '"'),
        ('--config="' + (Join-Path $repoRoot 'deploy/grafana/grafana.ini') + '"'))}
)

# Keep every verified previous service in the map before any launch. A failure
# while starting an earlier definition must not discard a later service's ownership.
$services = @{}
if (Test-Path -LiteralPath $statePath) {
    foreach ($saved in (Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json).services) {
        $definition = $definitions | Where-Object name -eq $saved.name | Select-Object -First 1
        if ($null -eq $definition -or $saved.executable -ne $definition.executable) {
            throw "Unrecognized dashboard ownership record: $($saved.name)."
        }
        $entry = [ordered]@{name=$saved.name; pid=$saved.pid; executable=$saved.executable;
            started_at=$saved.started_at; children=@($saved.children | Where-Object { $null -ne $_ })}
        $null = Get-OwnedService $entry
        Update-ExporterChildren $entry
        $services[$entry.name] = $entry
    }
}

foreach ($definition in $definitions) {
    $entry = $services[$definition.name]
    $process = Get-OwnedService $entry
    $liveChildren = @()
    if ($null -ne $entry) {
        $liveChildren = @($entry.children | Where-Object { $null -ne (Get-OwnedService $_) })
    }
    if ($null -eq $process -and $liveChildren.Count -eq 0) {
        $listener = Get-NetTCPConnection -State Listen -LocalPort $definition.port -ErrorAction SilentlyContinue
        if ($listener) { throw "Port $($definition.port) is already occupied by another service." }
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
        $logBase = Join-Path $runtimeRoot "logs/$($definition.name)-$stamp"
        $process = Start-Process -FilePath $definition.executable -ArgumentList $definition.arguments `
            -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput "$logBase.out.log" -RedirectStandardError "$logBase.err.log"
        $entry = [ordered]@{name=$definition.name; pid=$process.Id;
            executable=$definition.executable; started_at=$process.StartTime.ToUniversalTime().ToString('o'); children=@()}
        $services[$definition.name] = $entry
        Save-ServiceState $services
    }
    $ready = $false
    $startupClock = [Diagnostics.Stopwatch]::StartNew()
    Write-Output "Checking $($definition.name) readiness (up to $StartupTimeoutSeconds seconds)."
    # Windows cold starts can spend minutes loading the bundled Grafana plugins.
    # Keep ownership and HTTP/listener checks intact while allowing bounded startup.
    while ($startupClock.Elapsed.TotalSeconds -lt $StartupTimeoutSeconds) {
        Update-ExporterChildren $entry
        Save-ServiceState $services
        $liveIds = @()
        $ownedRoot = Get-OwnedService $entry
        if ($null -ne $ownedRoot) { $liveIds += $ownedRoot.Id }
        foreach ($child in @($entry.children)) {
            $ownedChild = Get-OwnedService $child
            if ($null -ne $ownedChild) { $liveIds += $ownedChild.Id }
        }
        if ($liveIds.Count -eq 0) {
            throw "Dashboard service $($definition.name) exited during startup. Inspect build/monitoring/logs."
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $definition.health -TimeoutSec 1
            $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $definition.port -ErrorAction SilentlyContinue)
            $ownedListener = @($listeners | Where-Object { $_.OwningProcess -in $liveIds })
            if ($response.StatusCode -eq 200 -and $ownedListener.Count -gt 0) { $ready = $true; break }
        } catch {
            # Startup migrations/imports can precede the local readiness endpoint.
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not $ready) { throw "Dashboard service $($definition.name) did not become ready within $StartupTimeoutSeconds seconds. Its owned process was left running; inspect build/monitoring/logs before retrying." }
}
$services.Values | ForEach-Object { [pscustomobject]$_ } | Sort-Object name | Format-Table name,pid
Write-Output 'Dashboard: http://localhost:3000/d/tradebot-system'
