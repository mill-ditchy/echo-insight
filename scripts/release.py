"""Check Git contents for private data and build a source-only release."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 5 * 1024 * 1024
PUBLIC_DIRECTORIES = {"src", "tests", "scripts", "web", "docs", "LICENSES", ".github", "android"}
PUBLIC_ROOT_FILES = {
    ".gitattributes", ".gitignore", "VERSION", "LICENSE", "README.md", "CHANGELOG.md",
    "CONTRIBUTING.md", "CONTRIBUTORS.md", "SECURITY.md", "app.py", "desktop.py", "webapp.py", "scheduler.py",
    "启动赛博情报局.bat", "mobile_server.py", "启动手机连接.ps1",
}
PLACEHOLDERS = {"data/.gitkeep", "output/.gitkeep", "knowledge/images/.gitkeep"}
SECRET_PATTERNS = (
    re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{24,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?")
COMPANION_FILES = {
    "mobile_server.py", "启动手机连接.ps1", "requirements-mobile.txt", "docs/ANDROID.md",
    "scripts/build_android.py", "scripts/preview_mobile.py", "src/security/mobile_access.py",
    "tests/test_mobile_access.py",
}


class ReleaseError(RuntimeError):
    pass


def git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True)
    if result.returncode:
        raise ReleaseError("Git operation failed. Check the repository and reference.")
    return result.stdout


def companion_path(name: str) -> bool:
    return name.startswith("android/") or name in COMPANION_FILES


def allowed_path(name: str, *, include_companion: bool = False) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        return False
    if not include_companion and companion_path(name):
        return False
    if name in PLACEHOLDERS or name in PUBLIC_ROOT_FILES:
        return True
    if len(path.parts) == 1:
        return bool(re.fullmatch(r"requirements(?:-[a-z0-9-]+)?\.(?:txt|lock)", name))
    if path.parts[0] == "config":
        return len(path.parts) == 2 and path.name.endswith(".example.json") and not path.name.startswith(".")
    if path.parts[0] == "android":
        source_types = {"src": {".java"}, "res": {".xml"}, "assets": {".js", ".css"}, "tests": {".py", ".js"}}
        if name != "android/AndroidManifest.xml" and not (
            len(path.parts) >= 3 and path.suffix.lower() in source_types.get(path.parts[1], set())
        ):
            return False
    # Reject hidden environment/browser state even if placed within a public directory.
    forbidden = {"__pycache__", ".venv", "node_modules", "secrets", ".git"}
    return path.parts[0] in PUBLIC_DIRECTORIES and not any(part.casefold() in forbidden or part.casefold().startswith(".env") for part in path.parts) and path.suffix.lower() not in {
        ".pyc", ".pyo", ".onnx", ".dll", ".exe", ".db", ".sqlite", ".log", ".tmp", ".download", ".zip", ".pfx", ".key",
        ".apk", ".aab", ".dex", ".class", ".p12", ".jks", ".keystore", ".pem",
    }


def git_files(root: Path, ref: str | None = None) -> list[tuple[str, str, str]]:
    files = []
    if ref is None:
        records = git(root, "ls-files", "--stage", "-z")
        for record in records.split(b"\0"):
            if not record:
                continue
            metadata, name = record.split(b"\t", 1)
            mode, oid, stage = metadata.decode("ascii").split()
            if stage != "0":
                raise ReleaseError("Resolve merge conflicts before releasing.")
            files.append((name.decode("utf-8"), mode, oid))
    else:
        oid = git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode("ascii").strip()
        records = git(root, "ls-tree", "-r", "-z", oid)
        for record in records.split(b"\0"):
            if not record:
                continue
            metadata, name = record.split(b"\t", 1)
            mode, kind, blob = metadata.decode("ascii").split()
            files.append((name.decode("utf-8"), mode, blob))
    return files


def read_blobs(root: Path, files: list[tuple[str, str, str]]) -> dict[str, bytes]:
    blob_ids = [oid for _, _, oid in files]
    batch_input = ("\n".join(blob_ids) + "\n").encode("ascii")
    metadata = git(root, "cat-file", "--batch-check", stdin=batch_input).decode("ascii").splitlines()
    for (name, _, _), line in zip(files, metadata):
        fields = line.split()
        if len(fields) != 3 or fields[1] != "blob" or int(fields[2]) > MAX_FILE_BYTES:
            raise ReleaseError(f"{name}: not a small source file; binary assets must stay local.")
    output = git(root, "cat-file", "--batch", stdin=batch_input)
    offset, blobs = 0, {}
    for name, _, _ in files:
        end = output.index(b"\n", offset)
        size = int(output[offset:end].split()[-1])
        offset = end + 1
        blobs[name] = output[offset:offset + size]
        offset += size + 1
    return blobs


def inspect_example(name: str, content: bytes) -> None:
    try:
        data = json.loads(content)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ReleaseError(f"{name}: invalid example JSON.") from exc
    if not isinstance(data, dict):
        raise ReleaseError(f"{name}: example JSON must be an object.")

    def inspect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in {"api_key", "cookie", "cookies", "credential_id", "refresh_token", "access_token", "password"} and item:
                    raise ReleaseError(f"{name}: example contains a nonempty credential field.")
                if key in {"sources", "profiles", "entries"} and item:
                    raise ReleaseError(f"{name}: example contains personal sources or knowledge.")
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)
    inspect(data)


def check(root: Path = ROOT, ref: str | None = None, *, include_companion: bool = False) -> dict[str, bytes]:
    files = git_files(root, ref)
    if not files:
        raise ReleaseError("No source files found. Stage the project first with git add .")
    for name, mode, _ in files:
        if not allowed_path(name, include_companion=include_companion):
            raise ReleaseError(f"{name}: private, generated or unexpected file must not be published.")
        if mode not in {"100644", "100755"}:
            raise ReleaseError(f"{name}: symlinks and submodules are not accepted in the source release.")
    blobs = read_blobs(root, files)
    for name, content in blobs.items():
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            raise ReleaseError(f"{name}: credential-like content detected; value omitted.")
        if name.startswith("config/"):
            inspect_example(name, content)
    return blobs


def release_version(blobs: dict[str, bytes]) -> str:
    try:
        version = blobs["VERSION"].decode("ascii").strip()
    except (KeyError, UnicodeDecodeError) as exc:
        raise ReleaseError("A VERSION file is required.") from exc
    if not VERSION_PATTERN.fullmatch(version):
        raise ReleaseError("VERSION must contain a semantic version such as 0.1.0.")
    return version


def build(root: Path = ROOT, output_dir: Path | None = None, ref: str = "HEAD", *, include_companion: bool = False) -> Path:
    blobs = check(root, ref, include_companion=include_companion)
    version = release_version(blobs)
    commit = git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode("ascii").strip()
    if git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise ReleaseError("Commit all source changes before building. Local ignored data is allowed.")
    if ref != "HEAD" and ref != "v" + version:
        raise ReleaseError("Release reference must be HEAD or the tag matching VERSION.")
    if commit != git(root, "rev-parse", "HEAD").decode("ascii").strip():
        raise ReleaseError("Check out the release commit before building.")
    output_dir = output_dir or root / "dist"
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"cyber-intelligence-agency-{version}-source"
    destination = output_dir / (stem + ".zip")
    archive = git(root, "archive", "--format=zip", f"--prefix=cyber-intelligence-agency-{version}/", commit)
    destination.write_bytes(archive)
    digest = hashlib.sha256(archive).hexdigest()
    (output_dir / (stem + ".sha256")).write_text(f"{digest}  {destination.name}\n", encoding="ascii")
    (output_dir / (stem + ".json")).write_text(json.dumps({
        "version": version, "commit": commit, "archive": destination.name, "sha256": digest,
        "source_files": len(blobs), "runtime": "Windows x64 / Python 3.11", "license": "MIT",
        "includes_credentials": False, "includes_models": False,
        "includes_companion": include_companion,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    checker = subparsers.add_parser("check", help="Check staged files, or a committed reference")
    checker.add_argument("--ref", default=None)
    checker.add_argument("--include-companion", action="store_true", help="Explicitly include the later companion release")
    builder = subparsers.add_parser("build", help="Build a clean, committed source release")
    builder.add_argument("--ref", default="HEAD")
    builder.add_argument("--output", type=Path, default=None)
    builder.add_argument("--include-companion", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "check":
            blobs = check(ref=args.ref, include_companion=args.include_companion)
            print(f"Release content check passed: {len(blobs)} source files. No private paths or recognized credentials.")
        else:
            print(build(output_dir=args.output, ref=args.ref, include_companion=args.include_companion))
        return 0
    except (ReleaseError, FileNotFoundError) as exc:
        print(f"Release stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
