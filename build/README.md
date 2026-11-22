# Build from source

The patch targets [Codex `rust-v0.171.0-alpha.13`](https://github.com/openai/codex/releases/tag/rust-v0.171.0-alpha.13) at `581161ab41e643d942e1e1b5f561034060bf18e3`. The prebuilt release uses the `dev-small` Cargo profile. The build uses Git and a Linux Docker engine. Rust runs in the container.

From the Oedipus repository root in PowerShell, run the following commands.

~~~powershell
git clone --depth 1 --branch rust-v0.171.0-alpha.13 https://github.com/openai/codex.git codex-upstream
$checkout = (Resolve-Path .\codex-upstream).Path
$oedipus = (Get-Location).Path
$dist = (New-Item -ItemType Directory -Path .\dist -Force).FullName
$image = 'ghcr.io/rust-cross/cargo-xwin@sha256:9856b895265d4966f212228ba64802cf89337e2a2a537aa2533c1b8784cbc81b'
docker run --rm -v oedipus-build:/out -v oedipus-rustup:/usr/local/rustup -v "${checkout}:/checkout:ro" -v "${oedipus}:/builder:ro" -v "${dist}:/deliver" $image bash /builder/build/build-windows-msvc.sh
~~~

The script verifies and applies the patch to the pinned commit. It verifies the pinned Windows V8 artifacts, matches the Windows migration bytes, stamps workspace lockfile versions, and writes `dist\codex-childfix-0171-crlf.exe`.

## Windows checks

Maintainers can use Python 3 to run the isolated fixtures with the matching official Codex Desktop installed.

~~~powershell
$binary = '.\dist\codex-childfix-0171-crlf.exe'
$desktop = Get-AppxPackage -Name 'OpenAI.Codex'
$stock = Join-Path $desktop.InstallLocation 'app\resources\codex.exe'
$patch = (Get-FileHash .\patches\steward-cas-child-prefix-0171.patch -Algorithm SHA256).Hash.ToLowerInvariant()
& $binary --steward-storage-self-test
python .\windows\smoke_native.py --binary $binary --patch-sha256 $patch --stock-binary $stock
python .\smoke_storage.py $binary --patched
~~~

The stock-profile check starts the candidate on databases seeded by the packaged backend and compares migration records before and after startup. The storage check uses a loopback model fixture for sub-agents and manual forks in legacy and paginated modes. A newly compiled executable needs a separately verified SHA-256 before packaging with the installer.
