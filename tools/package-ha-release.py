#!/usr/bin/env python3
"""Build a deterministic, source-only Home Assistant integration archive."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile


def package(output):
    root = Path(__file__).resolve().parents[1]
    component = root / "custom_components/meizu_remoter_gateway"
    version = json.loads((component / "manifest.json").read_text())["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Integration version must be major.minor.patch")
    files = sorted(component.rglob("*"))
    allowed_root = {"LICENSE", "README.md", "manifest.json", "services.yaml", "strings.json"}
    selected = []
    for path in files:
        if path.is_symlink():
            raise ValueError("Symlinks are not release source")
        if not path.is_file():
            continue
        relative = path.relative_to(component)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if not (len(relative.parts) == 1 and (path.name in allowed_root or path.suffix == ".py")
                or len(relative.parts) == 2 and relative.parts[0] == "translations" and path.suffix == ".json"):
            raise ValueError(f"Unexpected integration file: {relative}")
        selected.append(path)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"meizu_remoter_gateway-{version}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in selected:
            entry = zipfile.ZipInfo(path.relative_to(root).as_posix(), (1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            bundle.writestr(entry, path.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_suffix(".zip.sha256")
    checksum.write_text(f"{digest}  {archive.name}\n")
    return {"version": version, "archive": archive.name, "files": len(selected), "sha256": digest}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(package(arguments.output)))
