"""Read-only reference baseline manifest tool.

Records a reproducible, frozen manifest of the two reference repositories:

- ``E:\\钢筋仪软件开发``  (rebar inspector / 钢筋仪软件开发)
- ``E:\\UVA_GPR_system``  (legacy UAV-GPR)

The tool never writes into the reference repositories.  It only runs read-only
``git`` queries and SHA-256 hashes the candidate source files listed in the
spec file.  Every candidate file must exist, otherwise collection fails
fail-closed and no output is written.

Usage::

    python tools/migration/reference_manifest.py --spec SPEC.json
        [--out-json PATH] [--out-md PATH]

``--out-json`` and ``--out-md`` are optional; without them the JSON manifest
is printed to stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

TOOL_NAME = "uav-gpr-reference-manifest"
TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"
SPEC_SCHEMA_VERSION = "1.0"


class ManifestError(RuntimeError):
    """Raised when the reference baseline cannot be collected (fail-closed)."""


def _python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _now_utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _git(repo_root: Path, args: list[str]) -> str:
    """Run a read-only git command inside ``repo_root``."""
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise ManifestError(f"git {' '.join(args)!r} failed in {repo_root}: {detail}")
    return proc.stdout


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_tracked_status(repo_root: Path, rel_path: str) -> str:
    """Classify a candidate file as committed / staged / worktree-modified / untracked."""
    out = _git(repo_root, ["status", "--porcelain=v1", "--", rel_path])
    lines = [line for line in out.splitlines() if line.strip()]
    if not lines:
        return "committed"
    code = lines[0][:2]
    if code == "??":
        return "untracked"
    x, y = code[0], code[1]
    if x == " " and y != " ":
        return "worktree_modified"
    if x != " " and y == " ":
        return "staged"
    if x != " " and y != " ":
        return "staged_and_modified"
    return "changed"


@dataclass(frozen=True)
class FileEntry:
    role: str
    path: str
    sha256: str
    tracked_status: str

    def as_dict(self) -> dict[str, str]:
        return {
            "role": self.role,
            "path": self.path,
            "tracked_status": self.tracked_status,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class RepoManifest:
    repo_id: str
    name: str
    path: str
    branch: str
    head_sha: str
    dirty: bool
    worktree_status: tuple[str, ...]
    entries: tuple[FileEntry, ...]
    exclusions: tuple[str, ...]
    notes: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.repo_id,
            "name": self.name,
            "path": self.path,
            "branch": self.branch,
            "head_sha": self.head_sha,
            "worktree_dirty": self.dirty,
            "worktree_status": list(self.worktree_status),
            "exclusions": list(self.exclusions),
            "notes": self.notes,
            "files": [entry.as_dict() for entry in self.entries],
        }


@dataclass(frozen=True)
class Manifest:
    repositories: tuple[RepoManifest, ...]

    def as_dict(self, generated_at: str) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "tool": {
                "name": TOOL_NAME,
                "version": TOOL_VERSION,
                "python": _python_version(),
            },
            "generated_at": generated_at,
            "repositories": [repo.as_dict() for repo in self.repositories],
        }


def _validate_spec(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ManifestError("spec root must be a JSON object")
    spec_version = raw.get("schema_version")
    if spec_version != SPEC_SCHEMA_VERSION:
        raise ManifestError(f"unsupported spec schema_version: {spec_version!r}")
    repos_raw = raw.get("repositories")
    if not isinstance(repos_raw, list) or not repos_raw:
        raise ManifestError("spec must contain a non-empty 'repositories' list")
    repos: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for repo_raw in repos_raw:
        if not isinstance(repo_raw, dict):
            raise ManifestError("each repository entry must be a JSON object")
        repo_id = repo_raw.get("id")
        if not isinstance(repo_id, str) or not repo_id:
            raise ManifestError("repository entry requires a non-empty 'id'")
        if repo_id in seen_ids:
            raise ManifestError(f"duplicate repository id: {repo_id!r}")
        seen_ids.add(repo_id)
        for key in ("name", "path"):
            if not isinstance(repo_raw.get(key), str) or not repo_raw[key]:
                raise ManifestError(f"repository {repo_id!r} requires non-empty {key!r}")
        entries = repo_raw.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ManifestError(f"repository {repo_id!r} requires a non-empty 'entries' list")
        checked: list[dict[str, str]] = []
        seen_paths: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ManifestError(f"repository {repo_id!r}: entry must be an object")
            role = entry.get("role")
            rel = entry.get("path")
            if not isinstance(role, str) or not role:
                raise ManifestError(f"repository {repo_id!r}: entry requires 'role'")
            if not isinstance(rel, str) or not rel:
                raise ManifestError(f"repository {repo_id!r}: entry requires 'path'")
            rel_posix = rel.replace("\\", "/")
            path_obj = Path(rel_posix)
            if path_obj.is_absolute() or ".." in path_obj.parts:
                raise ManifestError(
                    f"repository {repo_id!r}: entry path must be relative without '..': {rel!r}"
                )
            if rel_posix in seen_paths:
                raise ManifestError(f"repository {repo_id!r}: duplicate entry path {rel_posix!r}")
            seen_paths.add(rel_posix)
            checked.append({"role": role, "path": rel_posix})
        exclusions = repo_raw.get("exclusions") or []
        if not isinstance(exclusions, list) or not all(
            isinstance(item, str) for item in exclusions
        ):
            raise ManifestError(f"repository {repo_id!r}: 'exclusions' must be a list of strings")
        excluded_globs = repo_raw.get("excluded_globs") or []
        if not isinstance(excluded_globs, list) or not all(
            isinstance(item, str) for item in excluded_globs
        ):
            raise ManifestError(
                f"repository {repo_id!r}: 'excluded_globs' must be a list of strings"
            )
        notes = repo_raw.get("notes", "")
        if not isinstance(notes, str):
            raise ManifestError(f"repository {repo_id!r}: 'notes' must be a string")
        for entry in checked:
            if any(fnmatch(entry["path"], pattern) for pattern in excluded_globs):
                raise ManifestError(
                    f"repository {repo_id!r}: entry {entry['path']!r} matches an "
                    f"excluded glob: {excluded_globs!r}"
                )
        repo: dict[str, Any] = dict(repo_raw)
        repo["entries"] = checked
        repo.pop("excluded_globs", None)
        repos.append(repo)
    return repos


def _collect_repo(repo_spec: dict[str, Any]) -> RepoManifest:
    repo_id = repo_spec["id"]
    repo_root = Path(repo_spec["path"])
    if not repo_root.is_dir():
        raise ManifestError(f"repository {repo_id!r}: path does not exist: {repo_root}")
    branch = _git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    head_sha = _git(repo_root, ["rev-parse", "HEAD"]).strip()
    if not head_sha or len(head_sha) != 40:
        raise ManifestError(f"repository {repo_id!r}: HEAD is not a full commit sha: {head_sha!r}")
    status_out = _git(repo_root, ["status", "--porcelain=v1"])
    status_lines = tuple(sorted(line for line in status_out.splitlines() if line.strip()))
    dirty = bool(status_lines)

    entries: list[FileEntry] = []
    for entry in sorted(repo_spec["entries"], key=lambda item: (item["role"], item["path"])):
        rel_path = entry["path"]
        abs_path = repo_root / rel_path
        if not abs_path.is_file():
            raise ManifestError(
                f"repository {repo_id!r}: candidate file missing: {rel_path!r}"
            )
        entries.append(
            FileEntry(
                role=entry["role"],
                path=rel_path,
                sha256=_sha256_file(abs_path),
                tracked_status=_file_tracked_status(repo_root, rel_path),
            )
        )
    return RepoManifest(
        repo_id=repo_id,
        name=repo_spec["name"],
        path=str(repo_root),
        branch=branch,
        head_sha=head_sha,
        dirty=dirty,
        worktree_status=status_lines,
        entries=tuple(entries),
        exclusions=tuple(repo_spec["exclusions"]),
        notes=repo_spec.get("notes", ""),
    )


def collect_manifest(spec_path: Path) -> Manifest:
    """Load a spec file and collect every repository entry (read-only)."""
    if not spec_path.is_file():
        raise ManifestError(f"spec file does not exist: {spec_path}")
    try:
        raw = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read spec {spec_path}: {exc}") from exc
    repos = _validate_spec(raw)
    return Manifest(repositories=tuple(_collect_repo(repo) for repo in repos))


def _dump_json(manifest: Manifest, generated_at: str) -> str:
    return json.dumps(
        manifest.as_dict(generated_at),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _dump_markdown(manifest: Manifest, generated_at: str) -> str:
    lines: list[str] = [
        "# 参考项目基线 manifest",
        "",
        f"- 工具: `{TOOL_NAME}` v{TOOL_VERSION}",
        f"- schema: `{SCHEMA_VERSION}`",
        f"- 生成时间 (UTC): {generated_at}",
        "",
    ]
    for repo in manifest.repositories:
        lines += [
            f"## {repo.name} (`{repo.repo_id}`)",
            "",
            f"- 仓库路径: `{repo.path}`",
            f"- branch: `{repo.branch}`",
            f"- HEAD: `{repo.head_sha}`",
            f"- worktree dirty: {repo.dirty}",
            "",
            "### worktree status",
            "",
        ]
        if repo.worktree_status:
            lines.append("```text")
            lines.extend(repo.worktree_status)
            lines.append("```")
        else:
            lines.append("```text")
            lines.append("(clean)")
            lines.append("```")
        lines += [
            "",
            "### 来源角色与候选文件",
            "",
            "| role | 文件 | tracked | SHA256 |",
            "|---|---|---|---|",
        ]
        for entry in repo.entries:
            lines.append(
                f"| {entry.role} | `{entry.path}` | {entry.tracked_status} | `{entry.sha256}` |"
            )
        lines += ["", "### 明确排除内容", ""]
        lines.extend(f"- {exclusion}" for exclusion in repo.exclusions)
        if repo.notes:
            lines += ["", f"> {repo.notes}"]
        else:
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _write_atomic(path: Path, content: str) -> None:
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the read-only reference baseline manifest."
    )
    parser.add_argument("--spec", required=True, help="path to the spec JSON file")
    parser.add_argument("--out-json", help="write JSON manifest to this path")
    parser.add_argument("--out-md", help="write Markdown manifest to this path")
    args = parser.parse_args(argv)
    try:
        manifest = collect_manifest(Path(args.spec))
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    generated_at = _now_utc_iso()
    json_text = _dump_json(manifest, generated_at)
    if args.out_json:
        _write_atomic(Path(args.out_json), json_text)
    else:
        sys.stdout.write(json_text)
    if args.out_md:
        _write_atomic(Path(args.out_md), _dump_markdown(manifest, generated_at))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
