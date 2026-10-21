#!/usr/bin/env python3
"""Build an unpackable Chromium extension ZIP without development/browser data."""
import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]


def package(output):
    source = ROOT / "extension"
    manifest = json.loads((source / "manifest.json").read_text())
    if manifest["manifest_version"] != 3:
        raise ValueError("Manifest V3 required")
    required = [manifest["background"]["service_worker"], manifest["options_page"], manifest["action"]["default_popup"], *manifest["icons"].values()]
    for name in required:
        if not (source / name).is_file():
            raise ValueError(f"Missing extension resource: {name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file() or path.suffix not in {".json", ".js", ".css", ".html", ".png"}:
                continue
            relative = path.relative_to(source).as_posix()
            if relative.startswith("offscreen/") or (path.name.startswith("manifest.") and path.name != "manifest.json"):
                continue
            info = ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP integrity failure")
        print(f"Built {output.name}: {len(archive.namelist())} files; Chromium {manifest['minimum_chrome_version']}+; version {manifest['version']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "panther-intent-capture.zip")
    package(parser.parse_args().output.resolve())
