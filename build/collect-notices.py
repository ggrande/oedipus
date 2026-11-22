"""Collect third-party notices from the resolved Windows build inputs."""

import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import zipfile


def notice_files(directory, *, recursive=False):
    candidates = directory.rglob("*") if recursive else directory.iterdir()
    return (
        path for path in candidates
        if path.is_file() and not path.is_symlink()
        and re.match(r"^(?:licen[cs]e|copying|copyright|notice|unlicense)(?:$|[_. -])",
                     path.name, re.IGNORECASE)
        and ".git" not in path.relative_to(directory).parts
    )


def main():
    checkout, output = map(Path, sys.argv[1:])
    cargo_home = Path(os.environ["CARGO_HOME"])
    lock = tomllib.loads((checkout / "Cargo.lock").read_text(encoding="utf-8"))
    tree = subprocess.check_output([
        "cargo", "+1.99.1", "tree", "--locked", "--offline", "--target",
        "x86_64-pc-windows-msvc", "-p", "codex-cli", "-e", "normal", "--prefix",
        "none", "--format", "{p}|{l}",
    ], cwd=checkout, text=True)
    active = {}
    for line in tree.splitlines():
        match = re.match(r"^([^ ]+) v([^ |]+).*?\|(.*?)(?: \(\*\))?$", line)
        if match:
            name, version, license_expression = match.groups()
            active[name, version] = license_expression

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as bundle:
        bundle.writestr("cargo-dependencies.tsv", "Package\tVersion\tDeclared license\n" + "".join(
            f"{name}\t{version}\t{license_expression}\n"
            for (name, version), license_expression in sorted(active.items())
        ))
        written = set()
        for package in lock["package"]:
            name, version = package["name"], package["version"]
            source = package.get("source", "")
            if (name, version) not in active or not source:
                continue
            if source.startswith("registry+"):
                candidates = list((cargo_home / "registry/src").glob(f"*/{name}-{version}"))
            elif source.startswith("git+"):
                url, revision = source.split("#", 1)
                project = url.split("?", 1)[0].rstrip("/").split("/")[-1].removesuffix(".git")
                candidates = list((cargo_home / "git/checkouts").glob(f"{project}-*/{revision[:7]}"))
            else:
                raise ValueError(f"Unknown Cargo source for {name}")
            if len(candidates) != 1:
                raise ValueError(f"Expected one checked-out source for {name} {version}")
            directory = candidates[0]
            recursive = source.startswith("git+") and "rusty_v8" in source
            for notice in sorted(notice_files(directory, recursive=recursive)):
                key = f"cargo/{name}-{version}/{notice.relative_to(directory).as_posix()}"
                if key not in written:
                    bundle.write(notice, key)
                    written.add(key)

        rust = (
            Path(os.environ["RUSTUP_HOME"]) / "toolchains/1.99.1-x86_64-unknown-linux-gnu/share/doc/rust"
        )
        bundle.write(rust / "COPYRIGHT-library.html", "rust/COPYRIGHT-library.html")
        for license_text in sorted((rust / "licenses").glob("*.txt")):
            bundle.write(license_text, f"rust/licenses/{license_text.name}")
        print(f"Collected {len(written)} Cargo notice files and Rust standard-library notices.")


if __name__ == "__main__":
    main()
