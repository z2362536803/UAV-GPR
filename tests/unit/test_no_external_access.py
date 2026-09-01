"""Guard tests: the default (non-hardware) suite must not touch external I/O.

Two equivalent failure probes:

1. AST scan: no default test module may import serial / USB / network roots.
   Only ``tests/hardware/`` is allowed to reference such facilities.
2. Path scan: no default test module may reference the two read-only reference
   repositories; the reference manifest tests operate on synthetic repositories
   only (see ``tests/unit/test_reference_manifest.py``).
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1]

# Roots that would access USB, serial ports or the external network.
FORBIDDEN_IMPORT_ROOTS = {
    "serial",
    "usb",
    "socket",
    "requests",
    "urllib",
    "http",
    "websocket",
    "websockets",
}

# The two read-only reference repositories must never appear in default tests.
# Built from escapes/concatenation so this module does not self-match.
FORBIDDEN_REFERENCE_PATH_PARTS = (
    "\u94a2\u7b4b\u4eea\u8f6f\u4ef6\u5f00\u53d1",
    "UVA" + "_GPR" + "_system",
)


def _default_test_modules() -> list[Path]:
    modules: list[Path] = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if "hardware" in path.parts:
            continue
        modules.append(path)
    return modules


def test_default_tests_do_not_import_serial_usb_or_network() -> None:
    modules = _default_test_modules()
    assert modules, "default test modules must exist"
    for module_path in modules:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in FORBIDDEN_IMPORT_ROOTS, (
                        f"{module_path.name} imports forbidden root {root!r}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module is not None:
                    root = node.module.split(".")[0]
                    assert root not in FORBIDDEN_IMPORT_ROOTS, (
                        f"{module_path.name} imports forbidden root {root!r}"
                    )


def test_default_tests_do_not_reference_reference_repositories() -> None:
    for module_path in _default_test_modules():
        text = module_path.read_text(encoding="utf-8")
        for part in FORBIDDEN_REFERENCE_PATH_PARTS:
            assert part not in text, (
                f"{module_path.name} references read-only reference path {part!r}"
            )


def test_hardware_directory_is_the_only_authorized_place() -> None:
    hardware_dir = TESTS_DIR / "hardware"
    assert hardware_dir.is_dir()
    # ISSUE-023 added the LibreVNA opt-in hardware tests; every hardware test
    # module must live here (and only here) so the AST guard below can exempt
    # this directory while scanning every other default test module.
    assert {path.name for path in hardware_dir.glob("*.py")} == {
        "test_hardware_sentinel.py",
        "test_librevna_hardware.py",
    }
