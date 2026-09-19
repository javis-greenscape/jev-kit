<#
.SYNOPSIS
    Thin PowerShell launcher for the native-Windows airlock doctor.
.DESCRIPTION
    Finds a Python interpreter and hands every argument straight through to
    install\windows_doctor.py. No decision is made here: the launcher exists
    so a human or an agent has something to double-click or paste that does
    not require knowing where python.exe lives.

    Run it with, for example:
        powershell -ExecutionPolicy Bypass -File install\windows-doctor.ps1
.NOTES
    Never needs Administrator. Never writes outside the user's profile.
#>
param([Parameter(ValueFromRemainingArguments = $true)] $Args)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here 'windows_doctor.py'

$python = $null
foreach ($candidate in @('py', 'python')) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($found) { $python = $found.Source; break }
}
if (-not $python) {
    Write-Error ("No Python found. Install Python from python.org (not the " +
                 "Microsoft Store stub), then re-run this script.")
    exit 2
}

if ((Split-Path $python -Leaf) -eq 'py.exe') {
    & $python -3 $script @Args
} else {
    & $python $script @Args
}
exit $LASTEXITCODE
