param(
    [Parameter(Mandatory)][string]$ProbeConfig,
    [switch]$PublishStatus,
    [switch]$RemoveProbeTask
)

$ErrorActionPreference = 'Stop'
$ProbeConfig = (Resolve-Path -LiteralPath $ProbeConfig).Path
$test = Get-Content -LiteralPath $ProbeConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$runtime = Split-Path -Parent $ProbeConfig
$gh = $test.githubCli

function Read-GitHub($Endpoint) {
    $json = & $gh api $Endpoint
    if ($LASTEXITCODE -ne 0) { throw "GitHub request failed: $Endpoint" }
    $json | ConvertFrom-Json
}

function Find-RulePush($Report) {
    $ref = $Report.policyRef
    if ($ref -notlike 'refs/heads/*') { $ref = "refs/heads/$ref" }
    $encodedRef = [Uri]::EscapeDataString($ref)
    $endpoint = "repos/$($Report.policyRepo)/activity?ref=$encodedRef&activity_type=push&per_page=100"
    $json = & $gh api --paginate --slurp $endpoint
    if ($LASTEXITCODE -ne 0) { throw "GitHub request failed: $endpoint" }
    $pages = (ConvertFrom-Json -InputObject ('{"pages":' + ($json -join "`n") + '}')).pages
    $pushes = @(foreach ($page in $pages) {
        $page | Where-Object { $_.ref -eq $ref -and $_.after -eq $Report.policyCommit -and $_.activity_type -eq 'push' }
    })
    if ($pushes.Count -ne 1 -or -not $pushes[0].timestamp) {
        throw "Expected one GitHub push of rule $($Report.policyCommit) to $ref."
    }
    $pushes[0]
}

try {
    $boot = [DateTimeOffset](Get-CimInstance Win32_OperatingSystem).LastBootUpTime
    $oldBoot = [DateTimeOffset]$test.previousBoot
    $armed = [DateTimeOffset]$test.armedAt
    if ($boot -le $oldBoot -or $boot -lt $armed) { throw 'Windows did not reboot.' }
    $event = Get-WinEvent -FilterHashtable @{
        LogName = 'System'
        ProviderName = 'Microsoft-Windows-Kernel-General'
        Id = 13
        StartTime = $armed.LocalDateTime
        EndTime = $boot.LocalDateTime
    } -ErrorAction SilentlyContinue | Select-Object -First 1

    $deadline = [DateTimeOffset]::UtcNow.AddMinutes(7)
    do {
        $state = Get-Content -LiteralPath (Join-Path $runtime 'state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $reportPath = Join-Path $runtime ('reports/{0}.json' -f $state.policyCommit)
        if ($state.done -and $state.policyCommit -ne $test.previousPolicyCommit -and (Test-Path -LiteralPath $reportPath)) { break }
        if ([DateTimeOffset]::UtcNow -gt $deadline) { throw 'No new rule processed after Windows restarted.' }
        Start-Sleep -Seconds 4
    } while ($true)

    $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $rulePush = Find-RulePush $report
    $published = [DateTimeOffset]$rulePush.timestamp
    $task = Get-ScheduledTask -TaskName 'OpenGuildKit Jenkins'
    $taskRun = [DateTimeOffset](Get-ScheduledTaskInfo -TaskName $task.TaskName).LastRunTime
    $javaPath = Join-Path $runtime 'java\bin\java.exe'
    $java = Get-CimInstance Win32_Process -Filter "Name='java.exe'" |
        Where-Object { $_.ExecutablePath -eq $javaPath -and $_.CommandLine -like '*--httpPort=52278*' } |
        Select-Object -First 1
    if (-not $java -or $task.State -ne 'Running' -or $taskRun -lt $boot) { throw 'The Jenkins startup task did not run after reboot.' }
    $started = [DateTimeOffset]$java.CreationDate
    $job = Join-Path $runtime "home/jobs/poll-central-rule/builds/$($report.jenkinsBuild)/build.xml"
    $buildXml = Get-Content -LiteralPath $job -Raw
    if ($buildXml -notmatch 'TimerTriggerCause') { throw 'Jenkins did not start from its timer.' }
    if ($report.targets.Count -ne $test.targets.Count) { throw 'Unexpected pull-request count.' }
    $baselineTargets = @{}
    foreach ($target in $test.targets) {
        $key = "$($target.repo)#$($target.number)"
        if ($baselineTargets.ContainsKey($key)) { throw "Duplicate baseline pull request: $key." }
        $baselineTargets.Add($key, $target)
    }
    foreach ($target in $report.targets) {
        $key = "$($target.repo)#$($target.number)"
        if (-not $baselineTargets.ContainsKey($key)) { throw "Unexpected pull request: $key." }
        $before = $baselineTargets[$key]
        $baselineTargets.Remove($key)
        $pr = Read-GitHub "repos/$($target.repo)/pulls/$($target.number)"
        if ($pr.state -ne 'open' -or $pr.head.sha -ne $before.commit -or $target.commit -ne $before.commit -or
            $target.result.policyCommit -ne $report.policyCommit -or $target.result.version -ne $test.expectedVersion -or
            $target.result.state -ne $before.result.state -or $target.result.maxMinutes -ne 30) {
            throw "Unexpected PR result in $($target.repo)."
        }
        $statuses = Read-GitHub "repos/$($target.repo)/commits/$($target.commit)/statuses?per_page=100"
        foreach ($context in @('openguildkit/jenkins', 'openguildkit/export-link-expiry')) {
            $status = $statuses | Where-Object context -eq $context | Select-Object -First 1
            if (-not $status -or $status.description -notlike "*$($report.policyCommit)*" -or $status.state -ne $target.result.state) {
                throw "Wrong status in $($target.repo) for $context."
            }
        }
    }

    $duringRestart = $published -ge $armed -and $published -lt $boot
    $afterShutdown = if ($event) { $published -ge [DateTimeOffset]$event.TimeCreated } else { $null }
    $receipt = @{
        previousBoot = $oldBoot.ToUniversalTime().ToString('o')
        armedAt = $armed.ToUniversalTime().ToString('o')
        windowsShutdown = $(if ($event) { ([DateTimeOffset]$event.TimeCreated).ToUniversalTime().ToString('o') })
        windowsBoot = $boot.ToUniversalTime().ToString('o')
        rulePublishedAt = $published.ToUniversalTime().ToString('o')
        rulePushActivityId = $rulePush.id
        publishedBeforeWindowsBoot = $duringRestart
        publishedAfterWindowsShutdownEvent = $afterShutdown
        publishedBeforeJenkinsStarted = $published -lt $started
        scheduledTaskStartedAt = $taskRun.ToUniversalTime().ToString('o')
        jenkinsStartedAt = $started.ToUniversalTime().ToString('o')
        jenkinsTrigger = 'TimerTrigger'
        publisherRun = $test.publisherRun
        report = $report
    }
    ConvertTo-Json -InputObject $receipt -Depth 12 | Set-Content -LiteralPath $test.output -Encoding utf8

    if ($PublishStatus) {
        $statusState = if ($duringRestart -and $afterShutdown -eq $true) { 'success' } else { 'error' }
        $argsForStatus = @('api', "repos/$($report.policyRepo)/statuses/$($test.controlCommit)", '-f', 'context=openguildkit/reboot-verified',
            '-f', "state=$statusState", '-f', "description=Windows reboot; rule $($report.policyCommit)")
        & $gh @argsForStatus
        if ($LASTEXITCODE -ne 0) { throw 'Could not publish the reboot witness status.' }
    }
    if ($RemoveProbeTask) {
        Unregister-ScheduledTask -TaskName 'OpenGuildKit Verify Windows Reboot' -Confirm:$false
    }
} catch {
    $_ | Out-String | Set-Content -LiteralPath (Join-Path $runtime 'reboot-verification-error.txt') -Encoding utf8
    exit 1
}
