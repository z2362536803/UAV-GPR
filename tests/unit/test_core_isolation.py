"""Guard test: core must stay free of Qt, hardware, file-format and network deps."""

from __future__ import annotations

import ast
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parents[2] / "src" / "uav_gpr" / "core"

# core -> Python standard library + numpy only (AGENTS.md section 9).
FORBIDDEN_ROOTS = {
    "PySide6",
    "PyQt5",
    "PyQt6",
    "h5py",
    "serial",
    "usb",
    "socket",
    "requests",
    "urllib",
    "http",
    "websockets",
}


def test_core_imports_no_forbidden_dependencies() -> None:
    assert CORE_DIR.is_dir()
    for module_path in sorted(CORE_DIR.glob("*.py")):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in FORBIDDEN_ROOTS, (
                        f"{module_path.name} imports forbidden dependency {root!r}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module is not None:
                    root = node.module.split(".")[0]
                    assert root not in FORBIDDEN_ROOTS, (
                        f"{module_path.name} imports forbidden dependency {root!r}"
                    )
