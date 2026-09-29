[CmdletBinding()]
param(
    [string]$ProjectRoot,
    [switch]$AllUsers
)

$ErrorActionPreference = 'Stop'
if (-not $ProjectRoot) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$Pythonw = Join-Path $ProjectRoot 'venv\Scripts\pythonw.exe'
$PanelScript = Join-Path $ProjectRoot 'scripts\panel.py'
foreach ($RequiredPath in @($Pythonw, $PanelScript)) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Overseer shortcut dependency was not found: $RequiredPath"
    }
}

$UserPrograms = [Environment]::GetFolderPath('Programs')
$Programs = if ($AllUsers) { [Environment]::GetFolderPath('CommonPrograms') } else { $UserPrograms }
if ($AllUsers) {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
    if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Installing shortcuts for all users requires an elevated PowerShell.'
    }
}
$LegacyFolder = Join-Path $UserPrograms 'Overseer Network'
$LauncherDirectory = Join-Path $ProjectRoot 'data\launcher'
$Compiler = Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$LauncherSource = Join-Path $ProjectRoot 'scripts\windows_launcher.cs'
if (-not (Test-Path -LiteralPath $Compiler)) {
    $Compiler = Join-Path $env:SystemRoot 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path -LiteralPath $Compiler)) {
    throw 'The Windows .NET Framework compiler was not found.'
}
$null = New-Item -ItemType Directory -Path $LauncherDirectory -Force
$OpenLauncher = Join-Path $LauncherDirectory 'Overseer.exe'
$StopLauncher = Join-Path $LauncherDirectory 'OverseerStop.exe'
foreach ($Target in @($OpenLauncher, $StopLauncher)) {
    $Staging = "$Target.build.exe"
    $CompilerArguments = @('/nologo', '/target:winexe', '/optimize+', '/reference:System.Windows.Forms.dll', "/out:$Staging")
    if ($Target -eq $StopLauncher) { $CompilerArguments += '/define:STOP_PANEL' }
    & $Compiler @CompilerArguments $LauncherSource
    if ($LASTEXITCODE -ne 0) { throw "Launcher compilation failed: $Target" }
    Move-Item -LiteralPath $Staging -Destination $Target -Force
}
$Shell = New-Object -ComObject WScript.Shell
if (-not ('OverseerShortcutIdentity' -as [type])) {
    Add-Type -Path (Join-Path $ProjectRoot 'scripts\shortcut_identity.cs')
}

function Set-OverseerShortcut {
    param(
        [string]$Directory,
        [string]$Name,
        [string]$Action,
        [int]$IconIndex
    )

    $Shortcut = $Shell.CreateShortcut((Join-Path $Directory "$Name.lnk"))
    $Shortcut.TargetPath = if ($Action -eq 'stop') { $StopLauncher } else { $OpenLauncher }
    $Shortcut.Arguments = ''
    $Shortcut.WorkingDirectory = $ProjectRoot
    $Shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,$IconIndex"
    $Shortcut.WindowStyle = 7
    $Shortcut.Description = "Overseer Network: $Name"
    $Shortcut.Save()
    [OverseerShortcutIdentity]::Set((Join-Path $Directory "$Name.lnk"), "Overseer.Network.$Action")
}

function Test-OverseerShortcut {
    param($Shortcut)

    if ($Shortcut.WorkingDirectory -ine $ProjectRoot) {
        return $false
    }

    $LegacyCommand = $Shortcut.TargetPath -ieq $env:ComSpec -and
        $Shortcut.Arguments -match [regex]::Escape((Join-Path $ProjectRoot 'overseer.cmd'))
    $DirectCommand = $Shortcut.TargetPath -ieq $Pythonw -and
        $Shortcut.Arguments -match [regex]::Escape($PanelScript)
    $NativeCommand = $Shortcut.TargetPath -in @($OpenLauncher, $StopLauncher)
    return $LegacyCommand -or $DirectCommand -or $NativeCommand
}

$OpenName = -join [char[]](0x041E, 0x0442, 0x043A, 0x0440, 0x044B, 0x0442, 0x044C, 0x0020, 0x043F, 0x0430, 0x043D, 0x0435, 0x043B, 0x044C)
$StopName = -join [char[]](0x041E, 0x0441, 0x0442, 0x0430, 0x043D, 0x043E, 0x0432, 0x0438, 0x0442, 0x044C, 0x0020, 0x043F, 0x0430, 0x043D, 0x0435, 0x043B, 0x044C)
$ShortcutPrefix = 'Overseer Network - '
$OpenShortcutName = $ShortcutPrefix + $OpenName
$StopShortcutName = $ShortcutPrefix + $StopName
Set-OverseerShortcut -Directory $Programs -Name $OpenShortcutName -Action 'open' -IconIndex 104
Set-OverseerShortcut -Directory $Programs -Name $StopShortcutName -Action 'stop' -IconIndex 109

foreach ($Directory in (@($Programs, $UserPrograms, $LegacyFolder) | Select-Object -Unique)) {
    if (-not (Test-Path -LiteralPath $Directory)) {
        continue
    }

    foreach ($Link in Get-ChildItem -LiteralPath $Directory -Filter '*.lnk' -File) {
        $Existing = $Shell.CreateShortcut($Link.FullName)
        $Keep = $Directory -ieq $Programs -and $Link.BaseName -in @($OpenShortcutName, $StopShortcutName)
        if ((Test-OverseerShortcut -Shortcut $Existing) -and -not $Keep) {
            Remove-Item -LiteralPath $Link.FullName
        }
    }
}

if (Test-Path -LiteralPath $LegacyFolder) {
    $LegacyContents = @(Get-ChildItem -LiteralPath $LegacyFolder -Force)
    if ($LegacyContents.Count -eq 0) {
        Remove-Item -LiteralPath $LegacyFolder
    }
}

Write-Output "Start menu shortcuts installed in: $Programs"
