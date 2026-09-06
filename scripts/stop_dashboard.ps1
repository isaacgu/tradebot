param([ValidateSet('grafana', 'prometheus', 'broker', 'exporter')]
    [string[]]$Name = @('grafana', 'prometheus', 'broker', 'exporter'))

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtimeRoot = Join-Path $repoRoot 'build/monitoring'
$statePath = Join-Path $runtimeRoot 'services.json'
if (-not (Test-Path -LiteralPath $statePath)) { Write-Output 'No dashboard ownership record.'; exit 0 }

function Get-OwnedProcess($entry) {
    if ($null -eq $entry) { return $null }
    $process = Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    $savedStart = if ($entry.started_at -is [datetime]) { $entry.started_at.ToUniversalTime() } else {
        [datetimeoffset]::Parse([string]$entry.started_at, [Globalization.CultureInfo]::InvariantCulture).UtcDateTime
    }
    if ($process.Path -ne $entry.executable -or
        $process.StartTime.ToUniversalTime().Ticks -ne $savedStart.Ticks) {
        throw "Refusing to stop reused PID $($entry.pid)."
    }
    return $process
}

function Save-ServiceState($entries) {
    $temporary = Join-Path $runtimeRoot ('services-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        $payload = @{services=@($entries.Values | Sort-Object name); dashboard='http://localhost:3000/d/tradebot-system'} |
            ConvertTo-Json -Depth 6
        [IO.File]::WriteAllText($temporary, $payload, (New-Object Text.UTF8Encoding($false)))
        [IO.File]::Replace($temporary, $statePath, [NullString]::Value)
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
    }
}

$expectedExecutables = @{
    exporter = Join-Path $runtimeRoot 'venv/Scripts/python.exe'
    broker = Join-Path $runtimeRoot 'venv/Scripts/python.exe'
    prometheus = Join-Path $runtimeRoot 'prometheus/prometheus.exe'
    grafana = Join-Path $runtimeRoot 'grafana/bin/grafana.exe'
}
$services = @{}
foreach ($saved in (Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json).services) {
    if (-not $expectedExecutables.ContainsKey($saved.name) -or
        $saved.executable -ne $expectedExecutables[$saved.name]) {
        throw "Refusing an unrecognized dashboard ownership record: $($saved.name)."
    }
    $services[$saved.name] = [ordered]@{name=$saved.name; pid=$saved.pid; executable=$saved.executable;
        started_at=$saved.started_at; children=@($saved.children | Where-Object { $null -ne $_ })}
}

# Stop the UI first and exporter last. All Python targets must be either the saved
# launcher or a saved/discovered exporter worker, never a sibling MT5 probe.
foreach ($serviceName in @('grafana', 'prometheus', 'broker', 'exporter')) {
    if ($serviceName -notin $Name) { continue }
    $entry = $services[$serviceName]
    if ($null -eq $entry) { continue }
    $process = Get-OwnedProcess $entry
    $children = @{}
    if ($entry.name -in @('exporter', 'broker')) {
        $module = if ($entry.name -eq 'broker') { 'tradebot.monitoring.broker_exporter' } else {
            'tradebot.monitoring.acquisition_exporter'
        }
        $modulePattern = '(?:^|\s)-m\s+' + [regex]::Escape($module) + '(?:\s|$)'
        foreach ($savedChild in @($entry.children)) {
            if ($null -ne (Get-OwnedProcess $savedChild)) { $children[[int]$savedChild.pid] = $savedChild }
        }
        if ($null -ne $process) {
            $queue = New-Object 'Collections.Generic.Queue[int]'
            $queue.Enqueue($process.Id)
            while ($queue.Count -gt 0) {
                $parentId = $queue.Dequeue()
                foreach ($candidate in @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $parentId")) {
                    if ($candidate.Name -notin @('python.exe', 'pythonw.exe') -or
                        $candidate.CommandLine -notmatch $modulePattern) {
                        continue
                    }
                    $worker = Get-Process -Id $candidate.ProcessId -ErrorAction SilentlyContinue
                    if ($null -eq $worker -or $worker.StartTime -lt $process.StartTime) { continue }
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
        Save-ServiceState $services
        foreach ($child in @($entry.children | Sort-Object started_at -Descending)) {
            $worker = Get-OwnedProcess $child
            if ($null -eq $worker) { continue }
            $identity = Get-CimInstance Win32_Process -Filter "ProcessId = $($worker.Id)"
            if ($null -eq $identity) { continue }
            if ($identity.Name -notin @('python.exe', 'pythonw.exe') -or
                $identity.CommandLine -notmatch $modulePattern) {
                throw "Refusing non-exporter Python worker PID $($worker.Id)."
            }
            # Revalidate immediately before termination to avoid stale PID ownership.
            $worker = Get-OwnedProcess $child
            if ($null -ne $worker) { Stop-Process -InputObject $worker }
        }
    }
    $process = Get-OwnedProcess $entry
    if ($null -ne $process) { Stop-Process -InputObject $process }
    $services.Remove($entry.name)
    Save-ServiceState $services
    Write-Output "Stopped dashboard service $($entry.name)."
}
