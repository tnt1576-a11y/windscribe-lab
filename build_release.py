"""Package the portable app and public source using explicit file lists."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from windscribe_lab import __version__

ROOT_FILES = (".gitignore", ".gitattributes", "README.md", "LICENSE", "THIRD_PARTY.md",
              "main.py", "run.ps1", "build.ps1", "build_release.py",
              "requirements.txt", "requirements-dev.txt")
PUBLIC_DOCS = ("EXPORTS.md", "preview-simulation.png", "preview-anti-censorship.png")
PUBLIC_ASSETS = ("app.ico", "check.png", "check@2x.png", "mixed.png", "mixed@2x.png",
                 "chevron-down.png", "chevron-down@2x.png", "chevron-up.png", "chevron-up@2x.png",
                 "report.html", "server_catalog.json", "version_info.txt")
FORBIDDEN_PARTS = frozenset((".qa", ".research", ".git", ".codex", ".agents", ".aws",
                            "memory", "memories", "rollout_summaries", "__pycache__", "local-data"))
FORBIDDEN_NAMES = frozenset(("agents.md", "skill.md", "memory.md", "ui_design.md", "validation.md",
                            "reference_review.md", "timing_review.md", "sample-report.html",
                            "preferences.json", "config.json"))
FORBIDDEN_SUFFIXES = frozenset((".db", ".log", ".dpapi", ".bak", ".backup", ".dmp", ".env", ".conf", ".ovpn"))


def check_path(path: Path):
    if any(part.casefold() in FORBIDDEN_PARTS for part in path.parts):
        raise ValueError(f"Private directory in package: {path}")
    if path.name.casefold() in FORBIDDEN_NAMES or path.suffix.casefold() in FORBIDDEN_SUFFIXES:
        raise ValueError(f"Private file in package: {path}")
    if path.name.casefold().startswith((".env", "memory.")) or "icuuc" in path.name.casefold() or "icudt" in path.name.casefold():
        raise ValueError(f"Unexpected package file: {path}")


def public_source_files(root: Path) -> list[Path]:
    files = [root / name for name in ROOT_FILES]
    files += [root / "docs" / name for name in PUBLIC_DOCS]
    files += [root / "assets" / name for name in PUBLIC_ASSETS]
    for folder in ("windscribe_lab", "tests"):
        files += sorted((root / folder).glob("*.py"))
    files += sorted((root / "licenses").glob("*.txt"))
    for path in files:
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Missing or unsafe source file: {path}")
        check_path(path.relative_to(root))
    return sorted(files)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(root: Path, runtime: Path, output: Path) -> dict:
    root, runtime, output = root.resolve(), runtime.resolve(), output.resolve()
    if not runtime.is_relative_to(root) or not output.is_relative_to(root):
        raise ValueError("Runtime and output must stay inside this project.")
    sources = public_source_files(root)
    executable = runtime / "WindscribeLab.exe"
    if not executable.is_file() or not (runtime / "_internal").is_dir():
        raise ValueError("Build the portable app first.")
    output.mkdir(parents=True, exist_ok=True)
    portable = output / f"WindscribeLab-{__version__}-windows-x64.zip"
    source_zip = output / f"WindscribeLab-{__version__}-source.zip"
    with tempfile.TemporaryDirectory(prefix=".package-", dir=output) as scratch:
        scratch_path = Path(scratch).resolve()
        if not scratch_path.is_relative_to(output):
            raise ValueError("Unexpected staging directory.")
        package = scratch_path / "WindscribeLab"
        package.mkdir()
        shutil.copy2(executable, package / executable.name)
        shutil.copytree(runtime / "_internal", package / "_internal")
        for name in ("README.md", "LICENSE", "THIRD_PARTY.md"):
            shutil.copy2(root / name, package / name)
        for name in PUBLIC_DOCS:
            target = package / "docs" / name
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(root / "docs" / name, target)
        (package / "licenses").mkdir()
        for license_file in sorted((root / "licenses").glob("*.txt")):
            shutil.copy2(license_file, package / "licenses" / license_file.name)
        payload = sorted(path for path in package.rglob("*") if path.is_file())
        for path in payload:
            check_path(path.relative_to(package))
        if digest(executable) != digest(package / executable.name):
            raise ValueError("Packaged executable differs from the built app.")
        manifest = "".join(digest(path) + "  " + path.relative_to(package).as_posix() + "\n" for path in payload)
        (package / "MANIFEST.sha256").write_text(manifest, encoding="utf-8")
        with zipfile.ZipFile(portable, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(package.rglob("*")):
                if path.is_file():
                    archive.write(path, "WindscribeLab/" + path.relative_to(package).as_posix())
        with zipfile.ZipFile(portable) as archive:
            if archive.testzip() is not None:
                raise ValueError("Portable archive CRC failure.")
            for line in manifest.splitlines():
                expected, relative = line.split("  ", 1)
                if hashlib.sha256(archive.read("WindscribeLab/" + relative)).hexdigest() != expected:
                    raise ValueError(f"Archive hash mismatch: {relative}")
    with zipfile.ZipFile(source_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sources:
            archive.write(path, "WindscribeLab-source/" + path.relative_to(root).as_posix())
    with zipfile.ZipFile(source_zip) as archive:
        if archive.testzip() is not None:
            raise ValueError("Source archive CRC failure.")
    hashes = output / "SHA256SUMS.txt"
    hashes.write_text("".join(digest(path) + "  " + path.name + "\n" for path in (portable, source_zip)), encoding="utf-8")
    return {"version": __version__, "portable": str(portable), "source": str(source_zip),
            "checksums": str(hashes), "source_files": len(sources), "binary_sha256": digest(executable),
            "archive_hashes_verified": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path("dist/WindscribeLab"))
    parser.add_argument("--output", type=Path, default=Path("dist/public"))
    args = parser.parse_args()
    print(json.dumps(build(Path(__file__).resolve().parent, args.runtime, args.output), indent=2))
