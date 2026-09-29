& (Join-Path (Split-Path -Parent $PSScriptRoot) 'overseer.cmd') open --notify
exit $LASTEXITCODE
