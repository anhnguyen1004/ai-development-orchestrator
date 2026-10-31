"""AST-based architectural boundary tests enforcing workflow package isolation."""

import ast
from pathlib import Path
import sys

WORKFLOW_DIR = Path(__file__).resolve().parent.parent.parent.parent / "src" / "core" / "workflow"

STDLIB_MODULES = set(sys.stdlib_module_names)
THIRD_PARTY_ALLOWED = {"pydantic"}
ALLOWED_INTERNAL_PREFIXES = ("src.core.contracts", "src.core.workflow")


def get_imported_modules(file_path: Path) -> list[tuple[str, int]]:
    """Parse a python file using AST and extract all imported module names with line numbers."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    imports: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # Relative import within src.core.workflow
                continue
            if node.module:
                imports.append((node.module, node.lineno))

    return imports


def test_workflow_ast_boundaries():
    """
    Verify that every file under src/core/workflow/ imports only:
    1. Python Standard Library modules (via sys.stdlib_module_names)
    2. Pydantic
    3. src.core.contracts.*
    4. Other modules within src.core.workflow
    
    Explicitly rejects dependencies on orchestration, agents, LLM, or business applications.
    """
    py_files = list(WORKFLOW_DIR.glob("*.py"))
    assert len(py_files) > 0, f"No python files found in {WORKFLOW_DIR}"

    violations: list[str] = []

    for py_file in py_files:
        imported = get_imported_modules(py_file)
        for mod_name, lineno in imported:
            top_level = mod_name.split(".")[0]

            # Rule 1: Internal imports must stay strictly inside allowed core packages
            if top_level == "src":
                if not any(mod_name.startswith(p) for p in ALLOWED_INTERNAL_PREFIXES):
                    violations.append(
                        f"{py_file.name}:{lineno} forbidden internal import '{mod_name}'"
                    )
                continue

            # Rule 2: Allowed third-party dependencies (Pydantic only)
            if top_level in THIRD_PARTY_ALLOWED:
                continue

            # Rule 3: Allowed standard library modules
            if top_level in STDLIB_MODULES:
                continue

            # Any other module is a boundary violation
            violations.append(
                f"{py_file.name}:{lineno} unauthorized dependency '{mod_name}'"
            )

    assert not violations, f"Architectural boundary violations detected:\n" + "\n".join(violations)

