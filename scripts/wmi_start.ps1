param(
    [Parameter(Mandatory=$true)]
    [string]$CommandLine,
    [string]$CurrentDirectory = (Get-Location).Path
)

$p = [wmiclass]"Win32_Process"
$r = $p.Create($CommandLine, $CurrentDirectory, $null)
if ($r.ReturnValue -eq 0) {
    Write-Output $r.ProcessId
} else {
    Write-Error "Failed with ReturnValue $($r.ReturnValue)"
    exit 1
}
