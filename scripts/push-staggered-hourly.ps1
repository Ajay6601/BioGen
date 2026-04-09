param(
    [string]$Branch = "main",
    [int]$IntervalSeconds = 3600
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

git fetch origin
$commits = @(git rev-list --reverse "origin/$Branch..HEAD")
if ($commits.Count -eq 0) {
    Write-Host "Nothing to push (already matches origin/$Branch)."
    exit 0
}

Write-Host "Pushing $($commits.Count) commit(s) to origin/$Branch; $IntervalSeconds s between pushes."
for ($i = 0; $i -lt $commits.Count; $i++) {
    $sha = $commits[$i]
    $short = $sha.Substring(0, [Math]::Min(7, $sha.Length))
    Write-Host ""
    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ($($i + 1)/$($commits.Count)) pushing $short ..."
    git push origin "${sha}:refs/heads/$Branch"
    if ($i -lt $commits.Count - 1) {
        Write-Host "Sleeping $IntervalSeconds s until next push..."
        Start-Sleep -Seconds $IntervalSeconds
    }
}

Write-Host ""
Write-Host "Finished."
