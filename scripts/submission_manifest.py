"""Build and verify a hash manifest for a local FinTechathon submission evidence set."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA = "aegis-submission-manifest/v1"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_revision(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    revision = result.stdout.strip().lower()
    if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", revision):
        return revision
    return None


def _source_tree_clean(root: Path) -> bool | None:
    """Report tracked working-tree cleanliness; untracked evidence files are not source edits."""
    try:
        result = subprocess.run(
            ["git", "diff-index", "--quiet", "HEAD", "--"],
            cwd=root,
            check=False,
            capture_output=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _artifact_path(root: Path, supplied: str | Path) -> tuple[Path, str]:
    root_resolved = root.resolve()
    candidate = Path(supplied)
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"Artifact must be inside the root directory: {supplied}") from exc
    if not resolved.is_file():
        raise ValueError(f"Artifact must be an existing regular file: {supplied}")
    return resolved, relative.as_posix()


def build_manifest(
    root: Path,
    artifacts: list[str | Path],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Hash explicitly selected artifacts and record local source/runtime metadata."""
    if not artifacts:
        raise ValueError("Provide at least one --artifact")
    root = root.resolve()
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for supplied in artifacts:
        path, relative = _artifact_path(root, supplied)
        if relative in seen:
            raise ValueError(f"Duplicate artifact path: {relative}")
        seen.add(relative)
        data = path.read_bytes()
        entries.append(
            {
                "path": relative,
                "size_bytes": len(data),
                "sha256": _sha256(data),
            }
        )
    entries.sort(key=lambda item: item["path"])
    payload: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "source_revision": _source_revision(root),
        "source_tree_clean": _source_tree_clean(root),
        "python_version": platform.python_version(),
        "artifacts": entries,
        "limitations": [
            "A manifest digest detects accidental or later changes; it is not a digital signature.",
            "A matching artifact hash does not prove who created an artifact or that its claims are true.",
            "Live Base Sepolia activity must be established by the evidence bundle's own verification checks.",
        ],
    }
    payload["manifest_sha256"] = _sha256(_canonical_json(payload).encode("utf-8"))
    return payload


def verify_manifest(manifest: object, root: Path) -> dict[str, Any]:
    """Verify manifest structure, digest, and each referenced artifact without network access."""
    if not isinstance(manifest, dict):
        raise TypeError("Submission manifest must be a JSON object")
    digest = manifest.get("manifest_sha256")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise ValueError("Submission manifest has a missing or malformed manifest_sha256")
    payload = dict(manifest)
    payload.pop("manifest_sha256")
    expected = _sha256(_canonical_json(payload).encode("utf-8"))
    if not hmac.compare_digest(digest, expected):
        raise ValueError("Submission manifest SHA-256 digest does not match its payload")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"Unsupported submission manifest schema: {manifest.get('schema')!r}")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("Submission manifest must list at least one artifact")

    root = root.resolve()
    seen: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict):
            raise TypeError("Submission manifest contains an invalid artifact record")
        relative = item.get("path")
        size = item.get("size_bytes")
        expected_hash = item.get("sha256")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ValueError("Artifact path must be a non-empty relative path")
        if ".." in Path(relative).parts:
            raise ValueError(f"Artifact path may not traverse parent directories: {relative}")
        if relative in seen:
            raise ValueError(f"Duplicate artifact path: {relative}")
        seen.add(relative)
        if type(size) is not int or size < 0:
            raise ValueError(f"Artifact size is invalid: {relative}")
        if not isinstance(expected_hash, str) or _SHA256_RE.fullmatch(expected_hash) is None:
            raise ValueError(f"Artifact SHA-256 is malformed: {relative}")
        path, normalized = _artifact_path(root, relative)
        if normalized != relative:
            raise ValueError(f"Artifact path is not canonical: {relative}")
        data = path.read_bytes()
        if len(data) != size:
            raise ValueError(f"Artifact size mismatch: {relative}")
        if not hmac.compare_digest(_sha256(data), expected_hash):
            raise ValueError(f"Artifact SHA-256 mismatch: {relative}")
    return manifest


def _render_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# Aegis Submission Evidence Manifest",
        "",
        f"- Schema: {manifest['schema']}",
        f"- Generated (UTC): {manifest['generated_at']}",
        f"- Source revision: `{manifest['source_revision'] or 'unavailable'}`",
        f"- Tracked source tree clean: `{manifest['source_tree_clean']}`",
        f"- Python: `{manifest['python_version']}`",
        f"- Manifest SHA-256: `{manifest['manifest_sha256']}`",
        "",
        "| Artifact | Size (bytes) | SHA-256 |",
        "| --- | ---: | --- |",
    ]
    for item in manifest["artifacts"]:
        path = item["path"].replace("|", "\\|")
        lines.append(f"| {path} | {item['size_bytes']} | `{item['sha256']}` |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {limitation}" for limitation in manifest["limitations"])
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build or verify a local hash manifest for selected submission evidence files."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="hash explicitly selected evidence artifacts")
    build.add_argument("--root", type=Path, default=Path("."))
    build.add_argument("--output", type=Path, default=Path("reports/submission-manifest.json"))
    build.add_argument("--artifact", action="append", required=True, help="artifact path, repeatable")
    verify = subparsers.add_parser("verify", help="verify manifest and referenced files offline")
    verify.add_argument("manifest", type=Path)
    verify.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)

    try:
        if args.command == "build":
            root = args.root.resolve()
            output = args.output if args.output.is_absolute() else root / args.output
            markdown = output.with_suffix(".md")
            output_resolved = output.resolve()
            markdown_resolved = markdown.resolve()
            for artifact in args.artifact:
                artifact_path, _ = _artifact_path(root, artifact)
                if artifact_path in {output_resolved, markdown_resolved}:
                    raise ValueError("Manifest output files cannot also be listed as artifacts")
            manifest = build_manifest(root, args.artifact)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            markdown.write_text(_render_markdown(manifest), encoding="utf-8")
            print(f"Manifest SHA-256: {manifest['manifest_sha256']}")
            print(f"JSON manifest: {output}")
            print(f"Markdown manifest: {markdown}")
            return 0

        manifest_path = args.manifest
        if not manifest_path.is_absolute():
            manifest_path = args.root.resolve() / manifest_path
        with manifest_path.open("r", encoding="utf-8") as stream:
            manifest = json.load(stream)
        verify_manifest(manifest, args.root)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"Submission manifest verification failed: {exc}", file=sys.stderr)
        return 2

    print("Submission manifest integrity and artifact hashes: PASS")
    print(f"Artifacts verified: {len(manifest['artifacts'])}")
    print(f"Manifest SHA-256: {manifest['manifest_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
