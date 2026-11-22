[CmdletBinding()]
param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$pin = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'pin.json') -Raw | ConvertFrom-Json
function Assert-Hash($Path, $Expected) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Missing file: $Path" }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -cne $Expected) { throw "File changed since validation: $Path (actual $actual)" }
}
Assert-Hash $pin.backend_path $pin.backend_sha256
Assert-Hash $pin.desktop_path $pin.desktop_sha256
$current = Get-AppxPackage -Name 'OpenAI.Codex' | Sort-Object Version -Descending | Select-Object -First 1
if (!$current -or [string]$current.Version -cne $pin.desktop_version -or
    (Join-Path $current.InstallLocation 'app\ChatGPT.exe') -ine $pin.desktop_path) {
    throw 'Desktop package changed. Revalidate and rebuild before using the patched backend.'
}
if ($pin.source_tag -ceq 'rust-v0.171.0-alpha.13') {
    if ($pin.stock_windows_migrations_verified -ne $true) {
        throw 'This ChildFix build is incompatible with the saved Windows migration history. Open stock Codex until the rebuilt ChildFix is installed.'
    }
    if (!$pin.code_mode_host_sha256) { throw 'Reinstall ChildFix to validate the Code Mode companion.' }
    Assert-Hash (Join-Path $PSScriptRoot 'codex-code-mode-host.exe') $pin.code_mode_host_sha256
}
if ($CheckOnly) { Write-Host 'Backend and desktop package match the installed pin.'; return }
if (Get-Process -Name ChatGPT -ErrorAction SilentlyContinue) {
    throw 'Close ChatGPT before launching this shortcut so the backend override takes effect.'
}
$env:CODEX_CLI_PATH = $pin.backend_path
$env:CODEX_STEWARD_CAS = '1'
$desktopAlias = (Get-Command chatgpt-desktop.exe -ErrorAction Stop).Source
Start-Process -FilePath $desktopAlias -WindowStyle Normal
Write-Host 'Desktop started with the pinned Child Storage Fix backend. Verify a live child run before relying on storage sharing.'
