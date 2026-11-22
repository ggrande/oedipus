# Oedipus ChildFix

Oedipus is an independent build of Codex for Windows. ChildFix stores inherited sub-agent history in shared, content-addressed blobs. A sub-agent keeps its own rollout and references the records it inherits. Paginated manual forks use Codex's parent-history references.

## Install the Windows preview

This release supports **Windows x64 with Codex Desktop 26.1117.4957.0** and its bundled `codex-cli 0.171.0-alpha.13`. Installation uses Windows PowerShell and the prebuilt ZIP. Rust, Python, Docker, and Visual Studio are unnecessary for installation.

1. Download `Oedipus-ChildFix-v0.1.0-windows-x64.zip` and `SHA256SUMS.txt` from the [v0.1.0 release](https://github.com/ggrande/oedipus/releases/tag/v0.1.0).
2. Check the ZIP with `Get-FileHash .\Oedipus-ChildFix-v0.1.0-windows-x64.zip -Algorithm SHA256` and compare it with `SHA256SUMS.txt`. Extract the ZIP to a new folder.
3. In Windows PowerShell, `cd` into the extracted folder and run `.\Install-ChildFix.ps1`. For a check without installation, run `.\Install-ChildFix.ps1 -CheckOnly`. The installer verifies the release executable and desktop package hashes and runs the storage self-test.
4. Finish active work, fully quit Codex Desktop, and open **Codex - Child Storage Fix** from the desktop. The shortcut starts the matching packaged app with the ChildFix backend for that launch.

The preview executable is unsigned. Follow the execution and signing policy on your machine. The shortcut checks the version and installed hashes each time. A Codex Desktop update may require a new ChildFix build.

## Storage and switching back

New child rollouts can depend on the `steward-cas-v1` folder under the active Codex profile. Keep it with the rollouts when moving or backing up that profile. The stock Codex shortcut remains available. To read a ChildFix rollout independently of the patched backend, export it to a new JSONL file first.

~~~powershell
$childFix = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\steward-native\childfix-0171-alpha13\codex.exe'
& $childFix --steward-export-rollout 'C:\path\to\child-rollout.jsonl' 'C:\path\to\new-plain-export.jsonl'
~~~

An export is a standalone file. It does not register the exported file as a chat.

## What was verified

- The Windows build preserves the original CRLF bytes in SQL migrations. It opened a stock-created profile with all 81 migration records intact across six SQLite databases.
- The installer copies the exact matching Code Mode host from the official installed desktop package and verifies its hash.
- A live Desktop sub-agent used shared records for its inherited context. A paginated manual fork referenced its parent and returned the inherited turns. Both history modes passed isolated storage tests.

For the patch, build recipe, and Windows checks, see [Build from source](build/README.md).
