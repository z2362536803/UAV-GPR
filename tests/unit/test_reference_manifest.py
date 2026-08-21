"""Tests for the read-only reference baseline manifest tool (ISSUE-001).

All tests use synthetic temporary git repositories; they never touch the two
real reference directories and never need write permission on them.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

TOOL_PATH = Path(__file__).resolve().parents[2] / "tools" / "migration" / "reference_manifest.py"


def _load_tool() -> Any:
    spec = importlib.util.spec_from_file_location("reference_manifest", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def tool() -> Any:
    return _load_tool()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "synthetic-repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "UAV-GPR Tester")
    _git(repo, "config", "user.email", "tester@example.invalid")
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _write_spec(
    tmp_path: Path,
    repo: Path,
    entries: list[dict[str, str]],
    *,
    exclusions: list[str] | None = None,
    excluded_globs: list[str] | None = None,
    repo_id: str = "synthetic",
) -> Path:
    spec = {
        "schema_version": "1.0",
        "repositories": [
            {
                "id": repo_id,
                "name": "synthetic reference repo",
                "path": str(repo),
                "notes": "test fixture",
                "exclusions": exclusions or [],
                "excluded_globs": excluded_globs or [],
                "entries": entries,
            }
        ],
    }
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def _payload(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key != "generated_at"}


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL_PATH), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_hash_stable_across_runs(tool: Any, tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        {
            "src/pkg/a.py": "alpha = 1\n",
            "src/pkg/b.py": "beta = 2\n",
            "docs/readme.md": "first\n",
        },
    )
    spec_path = _write_spec(
        tmp_path,
        repo,
        [
            {"role": "core", "path": "src/pkg/a.py"},
            {"role": "core", "path": "src/pkg/b.py"},
            {"role": "docs", "path": "docs/readme.md"},
        ],
    )
    first = tool.collect_manifest(spec_path)
    second = tool.collect_manifest(spec_path)
    assert first.as_dict("FIXED") == second.as_dict("FIXED")
    first_repo = first.repositories[0]
    assert [entry.sha256 for entry in first_repo.entries] == [
        entry.sha256 for entry in second.repositories[0].entries
    ]


def test_output_sorting_is_stable(tool: Any, tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        {
            "src/z.py": "z\n",
            "src/a.py": "a\n",
            "src/m.py": "m\n",
        },
    )
    spec_path = _write_spec(
        tmp_path,
        repo,
        [
            {"role": "zeta", "path": "src/z.py"},
            {"role": "alpha", "path": "src/a.py"},
            {"role": "alpha", "path": "src/m.py"},
        ],
    )
    manifest = tool.collect_manifest(spec_path)
    paths = [entry.path for entry in manifest.repositories[0].entries]
    assert paths == ["src/a.py", "src/m.py", "src/z.py"]
    again = tool.collect_manifest(spec_path)
    assert [entry.path for entry in again.repositories[0].entries] == paths


def test_dirty_worktree_is_recorded(tool: Any, tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        {
            "src/committed.py": "one\n",
            "src/modified.py": "two\n",
            "src/empty_marker.py": "kept\n",
        },
    )
    (repo / "src/modified.py").write_text("two\nmodified\n", encoding="utf-8")
    staged = repo / "src/staged.py"
    staged.write_text("staged content\n", encoding="utf-8")
    _git(repo, "add", "src/staged.py")
    staged_modified = repo / "src/staged_modified.py"
    staged_modified.write_text("first version\n", encoding="utf-8")
    _git(repo, "add", "src/staged_modified.py")
    staged_modified.write_text("second version\n", encoding="utf-8")
    untracked = repo / "src/untracked.py"
    untracked.write_text("new\n", encoding="utf-8")

    spec_path = _write_spec(
        tmp_path,
        repo,
        [
            {"role": "core", "path": "src/committed.py"},
            {"role": "core", "path": "src/modified.py"},
            {"role": "core", "path": "src/staged.py"},
            {"role": "core", "path": "src/staged_modified.py"},
            {"role": "core", "path": "src/untracked.py"},
        ],
    )
    manifest = tool.collect_manifest(spec_path)
    repo_manifest = manifest.repositories[0]
    assert repo_manifest.dirty is True
    statuses = {entry.path: entry.tracked_status for entry in repo_manifest.entries}
    assert statuses["src/committed.py"] == "committed"
    assert statuses["src/modified.py"] == "worktree_modified"
    assert statuses["src/staged.py"] == "staged"
    assert statuses["src/staged_modified.py"] == "staged_and_modified"
    assert statuses["src/untracked.py"] == "untracked"
    assert repo_manifest.head_sha == tool._git(repo, ["rev-parse", "HEAD"]).strip()


def test_missing_path_fails_closed(tool: Any, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"src/known.py": "ok\n"})
    spec_path = _write_spec(
        tmp_path,
        repo,
        [
            {"role": "core", "path": "src/known.py"},
            {"role": "core", "path": "src/does_not_exist.py"},
        ],
    )
    with pytest.raises(tool.ManifestError, match="candidate file missing"):
        tool.collect_manifest(spec_path)


def test_missing_repository_fails_closed(tool: Any, tmp_path: Path) -> None:
    missing = tmp_path / "no-such-repo"
    spec_path = _write_spec(tmp_path, missing, [{"role": "core", "path": "src/a.py"}])
    with pytest.raises(tool.ManifestError, match="path does not exist"):
        tool.collect_manifest(spec_path)


def test_cli_no_output_on_failure(tool: Any, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"src/ok.py": "ok\n"})
    spec_path = _write_spec(
        tmp_path,
        repo,
        [
            {"role": "core", "path": "src/ok.py"},
            {"role": "core", "path": "src/missing.py"},
        ],
    )
    out_json = tmp_path / "manifest.json"
    out_md = tmp_path / "manifest.md"
    result = _run_cli(
        "--spec",
        str(spec_path),
        "--out-json",
        str(out_json),
        "--out-md",
        str(out_md),
    )
    assert result.returncode == 2
    assert "candidate file missing" in result.stderr
    assert not out_json.exists()
    assert not out_md.exists()


def test_equivalent_manifest_on_unchanged_input(tool: Any, tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        {
            "src/core/a.py": "alpha\n",
            "src/core/b.py": "beta\n",
            "src/lib/c.py": "gamma\n",
        },
    )
    spec_path = _write_spec(
        tmp_path,
        repo,
        [
            {"role": "core", "path": "src/core/a.py"},
            {"role": "core", "path": "src/core/b.py"},
            {"role": "lib", "path": "src/lib/c.py"},
        ],
    )
    first_json = tmp_path / "first.json"
    first_md = tmp_path / "first.md"
    second_json = tmp_path / "second.json"
    first_result = _run_cli(
        "--spec",
        str(spec_path),
        "--out-json",
        str(first_json),
        "--out-md",
        str(first_md),
    )
    assert first_result.returncode == 0
    assert _run_cli("--spec", str(spec_path), "--out-json", str(second_json)).returncode == 0
    first = json.loads(first_json.read_text(encoding="utf-8"))
    second = json.loads(second_json.read_text(encoding="utf-8"))
    assert _payload(first) == _payload(second)
    assert first_md.exists()
    first_files = [entry["path"] for entry in first["repositories"][0]["files"]]
    second_files = [entry["path"] for entry in second["repositories"][0]["files"]]
    assert first_files == second_files
    assert first_files == sorted(first_files)


def test_cli_prints_json_to_stdout(tool: Any, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"src/a.py": "abc"})
    spec_path = _write_spec(tmp_path, repo, [{"role": "core", "path": "src/a.py"}])
    result = _run_cli("--spec", str(spec_path))
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["schema_version"] == "1.0"
    assert data["repositories"][0]["id"] == "synthetic"
    assert data["repositories"][0]["files"][0]["sha256"] == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_entry_matching_excluded_glob_fails_closed(tool: Any, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"src/forbidden.py": "no\n"})
    spec_path = _write_spec(
        tmp_path,
        repo,
        [{"role": "core", "path": "src/forbidden.py"}],
        excluded_globs=["src/forbidden*"],
    )
    with pytest.raises(tool.ManifestError, match="matches an excluded glob"):
        tool.collect_manifest(spec_path)


def test_sha256_matches_known_vector(tool: Any, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"src/a.py": "abc"})
    spec_path = _write_spec(tmp_path, repo, [{"role": "core", "path": "src/a.py"}])
    manifest = tool.collect_manifest(spec_path)
    # SHA-256 of the bytes "abc" is the standard published vector.
    assert manifest.repositories[0].entries[0].sha256 == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
