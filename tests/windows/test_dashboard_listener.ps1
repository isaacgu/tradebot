# Deterministic fixtures: load only the launcher's pure ownership-selection functions.
# No services, sockets, processes, or ownership records are created or modified.
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot '../../scripts/start_dashboard.ps1'
$parseErrors = $null
$tokens = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path $scriptPath), [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Launcher contains parse errors.' }
$wanted = @('Get-DashboardListeners', 'Test-DashboardOwnedListener', 'Get-OwnedService',
    'ConvertFrom-DashboardListenerTable')
$definitions = $ast.FindAll({param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in $wanted
}, $true)
if ($definitions.Count -ne $wanted.Count) { throw 'Required launcher functions are missing.' }
foreach ($definition in $definitions) { . ([scriptblock]::Create($definition.Extent.Text)) }

function Assert($condition, [string]$message) {
    if (-not $condition) { throw $message }
}
function Get-NetTCPConnection { param($LocalPort, $ErrorAction) return $script:reported }
function Get-NativeDashboardListeners {
    param($Port)
    $script:nativeCalls++
    if ($script:nativeFails) { throw 'Simulated ownership read failure.' }
    return $script:native
}
function Get-Process { param($Id, $ErrorAction) return $script:process }
function Endpoint($state, [int]$owner, [int]$remotePort=0, [string]$remoteAddress='0.0.0.0') {
    return [pscustomobject]@{LocalAddress='127.0.0.1'; LocalPort=8765;
        RemoteAddress=$remoteAddress; RemotePort=$remotePort; State=$state; OwningProcess=$owner}
}
$script:nativeCalls = 0
$script:nativeFails = $false
$script:reported = @(Endpoint $null 33184)
$script:native = @(Endpoint 'Listen' 33184)
$listeners = @(Get-DashboardListeners -Port 8765)
Assert ($script:nativeCalls -eq 1) 'Unmapped socket state must use listener-only ownership evidence.'
Assert (Test-DashboardOwnedListener $listeners[0] @(33184) 8765) 'Verified owned listener rejected.'
Assert (-not (Test-DashboardOwnedListener $listeners[0] @(99999) 8765)) 'Wrong PID accepted.'
Assert (-not (Test-DashboardOwnedListener $listeners[0] @(33184) 9090)) 'Wrong port accepted.'

$script:reported = @(Endpoint 'Listen' 0 57001 '127.0.0.1')
$script:native = @()
Assert (@(Get-DashboardListeners -Port 8765).Count -eq 0) 'PID-0 ghost became an occupied listener.'
$script:reported = @(Endpoint 'Established' 33184 57001 '127.0.0.1')
Assert (@(Get-DashboardListeners -Port 8765).Count -eq 0) 'Connected socket became a listener.'
Assert (-not (Test-DashboardOwnedListener (Endpoint 'Listen' 0) @(0) 8765)) 'PID zero accepted.'
Assert (-not (Test-DashboardOwnedListener (Endpoint 'Established' 33184) @(33184) 8765)) 'Connected state accepted.'

$script:reported = @(Endpoint $null 99999)
$script:native = @(Endpoint 'Listen' 99999)
Assert (@(Get-DashboardListeners -Port 8765).Count -eq 1) 'Foreign listener must block preflight.'
$script:nativeFails = $true
$failedClosed = $false
try { $null = Get-DashboardListeners -Port 8765 } catch { $failedClosed = $true }
Assert $failedClosed 'Unavailable ownership evidence was treated as a free port.'

$script:nativeFails = $false
$script:reported = @(Endpoint 'Listen' 33184)
$previousCalls = $script:nativeCalls
Assert (@(Get-DashboardListeners -Port 8765).Count -eq 1) 'Normal ownership record rejected.'
Assert ($script:nativeCalls -eq $previousCalls) 'Reliable normal state unnecessarily used fallback.'

$started = [datetime]'2026-10-15T06:28:00Z'
$script:process = [pscustomobject]@{Path='C:\owned\python.exe'; StartTime=$started}
$saved = [pscustomobject]@{name='fixture'; pid=33184; executable='C:\owned\python.exe';
    started_at=$started.ToUniversalTime().ToString('o')}
Assert ($null -ne (Get-OwnedService $saved)) 'Matching saved process identity rejected.'
$script:process.Path = 'C:\other\python.exe'
$mismatch = $false
try { $null = Get-OwnedService $saved } catch { $mismatch = $true }
Assert $mismatch 'Fallback weakened executable identity verification.'
$script:process.Path = $saved.executable
$script:process.StartTime = $started.AddSeconds(1)
$mismatch = $false
try { $null = Get-OwnedService $saved } catch { $mismatch = $true }
Assert $mismatch 'Fallback weakened creation-time identity verification.'

# Exercise the bounded native decoder with byte fixtures, including the observed
# state 0/1 metadata. Only this decoder consumes OWNER_PID_LISTENER API buffers.
$layout = @{rowSize=24; addressSize=4; addressOffset=4; portOffset=8;
    remoteAddressOffset=12; remotePortOffset=16; stateOffset=0; pidOffset=20}
$buffer = [Runtime.InteropServices.Marshal]::AllocHGlobal(28)
try {
    foreach ($rawState in @(0,1,2)) {
        $bytes = New-Object byte[] 28
        [BitConverter]::GetBytes([int]1).CopyTo($bytes, 0)
        [BitConverter]::GetBytes([int]$rawState).CopyTo($bytes, 4)
        ([byte[]]@(127,0,0,1)).CopyTo($bytes, 8)
        ([byte[]]@(34,61,0,0)).CopyTo($bytes, 12)
        [BitConverter]::GetBytes([int]33184).CopyTo($bytes, 24)
        [Runtime.InteropServices.Marshal]::Copy($bytes, 0, $buffer, 28)
        $decoded = @(ConvertFrom-DashboardListenerTable $buffer 28 $layout 8765)
        Assert ($decoded.Count -eq 1) 'Native listener fixture was lost.'
        Assert ($decoded[0].ObservedState -eq $rawState) 'Native raw state was not preserved.'
        Assert ($decoded[0].ListenerEvidence -eq 'OWNER_PID_LISTENER') 'Native selection proof missing.'
        Assert (Test-DashboardOwnedListener $decoded[0] @(33184) 8765) 'Native owned listener rejected.'
        Assert (-not (Test-DashboardOwnedListener $decoded[0] @(99999) 8765)) 'Native wrong PID accepted.'
    }
    # An arbitrary CLOSED row from the general table cannot use that proof.
    $script:reported = @(Endpoint 'Closed' 33184)
    $script:native = @()
    Assert (@(Get-DashboardListeners -Port 8765).Count -eq 0) 'General CLOSED row accepted as proof.'
    foreach ($badSize in @(0,3,27)) {
        $rejected = $false
        try { $null = ConvertFrom-DashboardListenerTable $buffer $badSize $layout 8765 }
        catch { $rejected = $true }
        Assert $rejected 'Partial native buffer was accepted.'
    }
    [Runtime.InteropServices.Marshal]::WriteInt32($buffer, 0, -1)
    $rejected = $false
    try { $null = ConvertFrom-DashboardListenerTable $buffer 28 $layout 8765 }
    catch { $rejected = $true }
    Assert $rejected 'Negative native row count was accepted.'
    [Runtime.InteropServices.Marshal]::WriteInt32($buffer, 0, 1)
    [Runtime.InteropServices.Marshal]::WriteInt32($buffer, 20, 123)
    $rejected = $false
    try { $null = ConvertFrom-DashboardListenerTable $buffer 28 $layout 8765 }
    catch { $rejected = $true }
    Assert $rejected 'Native connected remote port was accepted.'
    [Runtime.InteropServices.Marshal]::WriteInt32($buffer, 20, 0)
    [Runtime.InteropServices.Marshal]::WriteInt32($buffer, 16, 127)
    $rejected = $false
    try { $null = ConvertFrom-DashboardListenerTable $buffer 28 $layout 8765 }
    catch { $rejected = $true }
    Assert $rejected 'Native connected remote address was accepted.'
} finally {
    [Runtime.InteropServices.Marshal]::FreeHGlobal($buffer)
}
Write-Output 'Dashboard listener ownership fixtures passed.'
