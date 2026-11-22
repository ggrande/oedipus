[CmdletBinding()]
param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
$sourceTag = 'rust-v0.171.0-alpha.13'
$sourceCommit = '581161ab41e643d942e1e1b5f561034060bf18e3'
$patchHash = '8b50027e494c4a55def8a3299d24a3663ee88cebfeb0590ed8754adb698bc7da'
$desktopVersion = '26.1117.4957.0'
$stockHash = '4069acdd8d101830f63ea5e2775883bfecf8a152ae87ee1b0ef9f14204f850f9'
$buildHash = 'e15852fa509411978be6a0dbd5a7dcf242aaaad390966bda4d3bd2ab7f7b545a'
$codeModeHash = '753d65baafa0cafa62eb7f0a43f934421fc2eb67cc100c6ff0d8f9749a1ac5f6'
$candidate = Join-Path $PSScriptRoot 'codex-childfix.exe'
$launcherSource = Join-Path $PSScriptRoot 'Launch-ChildFix.ps1'

function Assert-Hash([string]$Path, [string]$Expected) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Missing file: $Path" }
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ine $Expected) {
        throw "SHA-256 mismatch: $Path"
    }
}

function Copy-Verified([string]$Source, [string]$Destination, [string]$Expected) {
    if (!(Test-Path -LiteralPath $Destination)) {
        $staged = "$Destination.installing"
        Copy-Item -LiteralPath $Source -Destination $staged -Force
        Assert-Hash $staged $Expected
        Move-Item -LiteralPath $staged -Destination $Destination
    }
    Assert-Hash $Destination $Expected
}

$desktop = Get-AppxPackage -Name 'OpenAI.Codex' | Sort-Object Version -Descending | Select-Object -First 1
if (!$desktop -or [string]$desktop.Version -cne $desktopVersion) {
    throw "This preview requires Windows x64 Codex Desktop $desktopVersion."
}
$desktopExe = Join-Path $desktop.InstallLocation 'app\ChatGPT.exe'
$resources = Join-Path $desktop.InstallLocation 'app\resources'
$stockCodeMode = Join-Path $resources 'codex-code-mode-host.exe'
Assert-Hash (Join-Path $resources 'codex.exe') $stockHash
Assert-Hash $stockCodeMode $codeModeHash
Assert-Hash $candidate $buildHash
if (!(Test-Path -LiteralPath $launcherSource -PathType Leaf)) { throw "Missing launcher: $launcherSource" }
$desktopHash = (Get-FileHash -LiteralPath $desktopExe -Algorithm SHA256).Hash.ToLowerInvariant()
& $candidate --steward-storage-self-test
if ($LASTEXITCODE -ne 0) { throw 'ChildFix failed its native storage self-test.' }
if ($CheckOnly) { Write-Host 'Windows package, release binary, Code Mode companion, and native self-test verified.'; return }

$dest = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'OpenAI\Codex\steward-native\childfix-0171-alpha13'
$backend = Join-Path $dest 'codex.exe'
$launcher = Join-Path $dest 'Launch-ChildFix.ps1'
New-Item -ItemType Directory -Path $dest -Force | Out-Null
Copy-Verified $candidate $backend $buildHash
Copy-Verified $stockCodeMode (Join-Path $dest 'codex-code-mode-host.exe') $codeModeHash

[ordered]@{
    source_tag = $sourceTag
    source_commit = $sourceCommit
    stock_sha256 = $stockHash
    patch_sha256 = $patchHash
    backend_path = $backend
    backend_sha256 = $buildHash
    code_mode_host_sha256 = $codeModeHash
    stock_windows_migrations_verified = $true
    desktop_path = $desktopExe
    desktop_sha256 = $desktopHash
    desktop_version = $desktopVersion
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dest 'pin.json') -Encoding UTF8
Copy-Item -LiteralPath $launcherSource -Destination $launcher -Force
& $launcher -CheckOnly

$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Codex - Child Storage Fix.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
$shortcut.TargetPath = (Get-Command powershell.exe -ErrorAction Stop).Source
$shortcut.Arguments = '-NoProfile -File "' + $launcher + '"'
$shortcut.WorkingDirectory = $dest
$shortcut.IconLocation = "$desktopExe,0"
$shortcut.Save()
Write-Host "Installed $shortcutPath"
