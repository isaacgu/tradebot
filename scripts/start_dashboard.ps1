param(
    [string]$BrokerTerminal = 'C:\Program Files\MetaTrader 5\terminal64.exe',
    [ValidateNotNullOrEmpty()]
    [ValidatePattern('^[^\x00-\x1F"]+$')]
    [string]$Gate1Report = 'build/gate1/30day-stable-b102ecdd/report.json',
    [ValidateNotNullOrEmpty()]
    [ValidatePattern('^[^\x00-\x1F"]+$')]
    [string]$ReferenceReport = 'build/gate1/source-clock-reference-acceptance-20260906-v1/report.json',
    [switch]$OpenBrowser,
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

function ConvertFrom-DashboardListenerTable([IntPtr]$Buffer, [int]$Size, $Layout, [int]$Port) {
    if ($Buffer -eq [IntPtr]::Zero -or $Size -lt 4) { throw 'Invalid TCP listener table buffer.' }
    $count = [Runtime.InteropServices.Marshal]::ReadInt32($Buffer)
    if ($count -lt 0 -or 4 + ([long]$count * $Layout.rowSize) -gt $Size) {
        throw 'Invalid TCP listener table row count.'
    }
    for ($index=0; $index -lt $count; $index++) {
        $row = [IntPtr]::Add($Buffer, 4 + $index * $Layout.rowSize)
        $rawPort = [Runtime.InteropServices.Marshal]::ReadInt32($row, $Layout.portOffset)
        $localPort = (($rawPort -band 255) -shl 8) -bor (($rawPort -shr 8) -band 255)
        if ($localPort -ne $Port) { continue }
        $remote = New-Object byte[] $Layout.addressSize
        [Runtime.InteropServices.Marshal]::Copy(
            [IntPtr]::Add($row, $Layout.remoteAddressOffset), $remote, 0, $remote.Length)
        if (@($remote | Where-Object { $_ -ne 0 }).Count -gt 0 -or
            [Runtime.InteropServices.Marshal]::ReadInt32($row, $Layout.remotePortOffset) -ne 0) {
            throw 'Listener-only table returned a connected-shaped endpoint.'
        }
        $address = New-Object byte[] $Layout.addressSize
        [Runtime.InteropServices.Marshal]::Copy(
            [IntPtr]::Add($row, $Layout.addressOffset), $address, 0, $address.Length)
        # OWNER_PID_LISTENER selects listening endpoints. On this Windows build,
        # even that table reports raw state 0/1 for a healthy owned listener. Keep
        # the raw value visible; infer Listen only from this native query contract
        # plus an unspecified remote endpoint, never from general-table CLOSED rows.
        [pscustomobject]@{LocalAddress=([Net.IPAddress]::new($address)).ToString();
            LocalPort=$localPort; State='Listen'; ListenerEvidence='OWNER_PID_LISTENER';
            ObservedState=[Runtime.InteropServices.Marshal]::ReadInt32($row, $Layout.stateOffset);
            OwningProcess=[Runtime.InteropServices.Marshal]::ReadInt32($row, $Layout.pidOffset)}
    }
}

function Get-NativeDashboardListeners([int]$Port) {
    # Windows can misreport states in OWNER_PID_ALL (including Get-NetTCPConnection).
    # Query OWNER_PID_LISTENER directly: this still proves actual socket ownership.
    # https://learn.microsoft.com/windows/win32/api/iphlpapi/nf-iphlpapi-getextendedtcptable
    if (-not ('TradebotDashboardTcpTable' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class TradebotDashboardTcpTable {
    [DllImport("iphlpapi.dll", SetLastError = true)]
    public static extern uint GetExtendedTcpTable(IntPtr table, ref int size,
        bool sorted, int family, int tableClass, uint reserved);
}
'@
    }
    foreach ($layout in @(
        @{family=2; rowSize=24; addressSize=4; addressOffset=4; portOffset=8;
            remoteAddressOffset=12; remotePortOffset=16; stateOffset=0; pidOffset=20},
        @{family=23; rowSize=56; addressSize=16; addressOffset=0; portOffset=20;
            remoteAddressOffset=24; remotePortOffset=44; stateOffset=48; pidOffset=52}
    )) {
        $size = 0
        $result = [TradebotDashboardTcpTable]::GetExtendedTcpTable(
            [IntPtr]::Zero, [ref]$size, $false, $layout.family, 3, 0)
        if ($result -ne 122) { throw "Cannot size TCP listener ownership table: $result." }
        $completed = $false
        for ($attempt=0; $attempt -lt 3; $attempt++) {
            if ($size -lt 4 -or $size -gt 16777216) { throw 'Invalid TCP listener table size.' }
            $allocated = $size
            $buffer = [Runtime.InteropServices.Marshal]::AllocHGlobal($allocated)
            try {
                $result = [TradebotDashboardTcpTable]::GetExtendedTcpTable(
                    $buffer, [ref]$size, $false, $layout.family, 3, 0)
                if ($result -eq 122) { continue }
                if ($result -ne 0) { throw "Cannot read TCP listener ownership table: $result." }
                if ($size -gt $allocated) { throw 'TCP listener table exceeded its buffer.' }
                ConvertFrom-DashboardListenerTable -Buffer $buffer -Size $size -Layout $layout -Port $Port
                $completed = $true
                break
            } finally {
                [Runtime.InteropServices.Marshal]::FreeHGlobal($buffer)
            }
        }
        if (-not $completed) { throw 'TCP listener ownership table changed repeatedly; retry later.' }
    }
}

function Get-DashboardListeners([int]$Port) {
    $reported = @(Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue)
    $shaped = @($reported | Where-Object {
        $_.RemotePort -eq 0 -and $_.RemoteAddress -in @('0.0.0.0', '::')
    })
    $reliable = @($shaped | Where-Object { $_.State -eq 'Listen' -and $_.OwningProcess -gt 0 })
    if ($reliable.Count -gt 0 -and $reliable.Count -eq $shaped.Count) { return $reliable }
    # Missing/unmapped states or PID-0 ghosts are not ownership evidence. The
    # native listener-only table is authoritative; a read failure must not mean free.
    return @(Get-NativeDashboardListeners -Port $Port)
}

function Test-DashboardOwnedListener($Listener, [int[]]$OwnedIds, [int]$Port) {
    return ($Listener.LocalAddress -eq '127.0.0.1' -and $Listener.LocalPort -eq $Port -and
        $Listener.State -eq 'Listen' -and $Listener.OwningProcess -gt 0 -and
        $Listener.OwningProcess -in $OwnedIds)
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
        '--reference-report', ('"' + $ReferenceReport + '"'),
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
        $listener = @(Get-DashboardListeners -Port $definition.port)
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
            $listeners = @(Get-DashboardListeners -Port $definition.port)
            $ownedListener = @($listeners | Where-Object {
                Test-DashboardOwnedListener -Listener $_ -OwnedIds $liveIds -Port $definition.port
            })
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
Write-Output 'Selected report paths are shown in the dashboard. Existing observers retain their startup paths.'
if ($OpenBrowser) {
    Start-Process 'http://localhost:3000/d/tradebot-system'
}
