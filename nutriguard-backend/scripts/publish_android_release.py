"""Publish a signed Android APK atomically to the local release feed.

Usage: python scripts/publish_android_release.py APK VERSION_CODE VERSION_NAME [NOTES]
Run on the backend host after copying the signed APK there. Does not deploy code.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) not in (4, 5):
        raise SystemExit("usage: publish_android_release.py APK VERSION_CODE VERSION_NAME [NOTES]")
    source = Path(sys.argv[1]).resolve(strict=True)
    version_code = int(sys.argv[2])
    version_name = sys.argv[3].strip()
    notes = sys.argv[4].strip() if len(sys.argv) == 5 else ""
    if version_code <= 0 or not version_name or len(version_name) > 64 or len(notes) > 500:
        raise SystemExit("invalid release metadata")
    if not source.is_file() or source.suffix.lower() != ".apk":
        raise SystemExit("source must be an APK file")
    if not 1 <= source.stat().st_size <= 150 * 1024 * 1024:
        raise SystemExit("APK must be between 1 byte and 150 MiB")
    # A release feed must never advertise an unsigned or unverifiable archive.
    signature = subprocess.run(
        ["apksigner", "verify", "--print-certs", str(source)],
        check=True, capture_output=True, text=True,
    ).stdout
    certificate = re.search(r"Signer #1 certificate SHA-256 digest: ([0-9a-f]{64})", signature)
    if certificate is None or "CN=Android Debug" in signature:
        raise SystemExit("APK must have a non-debug signing certificate")
    package_info = subprocess.run(
        ["aapt", "dump", "badging", str(source)],
        check=True, capture_output=True, text=True,
    ).stdout
    package = re.search(r"package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']*)'", package_info)
    if package is None or package.group(1) != "com.aistudio.nutriguard.analyzer" or int(package.group(2)) != version_code or package.group(3) != version_name:
        raise SystemExit("APK package or version does not match release metadata")

    target_dir = Path(__file__).resolve().parents[1] / "android-releases"
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest = target_dir / "latest.json"
    if manifest.exists():
        current = json.loads(manifest.read_text(encoding="utf-8"))
        if version_code <= int(current["versionCode"]):
            raise SystemExit("versionCode must increase")
        if current.get("signerSha256") != certificate.group(1):
            raise SystemExit("APK signing certificate differs from the previous release")

    target = target_dir / f"{version_code}.apk"
    temporary = target_dir / f".{version_code}.apk.tmp"
    digest = hashlib.sha256()
    try:
        with source.open("rb") as reader, temporary.open("wb") as writer:
            while chunk := reader.read(1024 * 1024):
                digest.update(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

    data = {
        "versionCode": version_code,
        "versionName": version_name,
        "sha256": digest.hexdigest(),
        "sizeBytes": target.stat().st_size,
        "notes": notes,
        "signerSha256": certificate.group(1),
    }
    temporary_manifest = target_dir / ".latest.json.tmp"
    try:
        with temporary_manifest.open("w", encoding="utf-8") as writer:
            json.dump(data, writer, ensure_ascii=False)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary_manifest, manifest)
    finally:
        temporary_manifest.unlink(missing_ok=True)
    print(f"Published Android {version_name} ({version_code}); SHA-256 {data['sha256']}")


if __name__ == "__main__":
    main()
