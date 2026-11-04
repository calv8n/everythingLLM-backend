from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass


# Import names that differ from their pip distribution names.
IMPORT_TO_DISTRIBUTION = {
    "PIL": "Pillow",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "fitz": "PyMuPDF",
    "google": "google-api-python-client",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
}


def _distribution_key(requirement: str) -> str:
    """Normalize a PEP 508-ish requirement string for duplicate detection."""
    name = re.split(r"[<>=!~;\[]", requirement.strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    requirements: list[str]
    error: str | None = None
    discovered_imports: tuple[str, ...] = ()


class ValidationAgent:
    """Static-check generated Python and reconcile imports with pip requirements."""

    _execution_failure_patterns = (
        re.compile(r"^\s*an error occurred\b", re.IGNORECASE),
        re.compile(r"^\s*error fetching\b", re.IGNORECASE),
        re.compile(r"^\s*failed to fetch\b", re.IGNORECASE),
        re.compile(r"^\s*failed to\b", re.IGNORECASE),
        re.compile(r"^\s*traceback \(most recent call last\):", re.IGNORECASE),
        re.compile(r"^\s*[\w.]+(?:Error|Exception):"),
    )

    def validate(self, code: str, declared_requirements: list[str]) -> ValidationResult:
        try:
            syntax_tree = ast.parse(code, filename="main.py")
            compile(syntax_tree, "main.py", "exec")
        except (SyntaxError, ValueError, TypeError) as error:
            line = getattr(error, "lineno", None)
            location = f" at line {line}" if line else ""
            return ValidationResult(
                valid=False,
                requirements=list(declared_requirements),
                error=f"Generated Python failed syntax validation{location}: {error}",
            )

        declared = list(dict.fromkeys(req.strip() for req in declared_requirements if req.strip()))
        declared_keys = {_distribution_key(req) for req in declared}
        discovered: list[str] = []
        for node in ast.walk(syntax_tree):
            if isinstance(node, ast.Import):
                module_names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_names = [node.module.split(".", 1)[0]]
            else:
                continue

            for module in module_names:
                if module in sys.stdlib_module_names or module == "__future__":
                    continue
                distribution = IMPORT_TO_DISTRIBUTION.get(module, module)
                key = _distribution_key(distribution)
                if key not in declared_keys:
                    declared.append(distribution)
                    declared_keys.add(key)
                if module not in discovered:
                    discovered.append(module)

        return ValidationResult(
            valid=True,
            requirements=declared,
            discovered_imports=tuple(discovered),
        )

    def validate_execution_output(self, output: str) -> str | None:
        """Return a repair diagnostic when stdout explicitly reports task failure."""
        for line in output.splitlines():
            if any(pattern.search(line) for pattern in self._execution_failure_patterns):
                return (
                    "Validation Agent detected a failure reported by the generated script. "
                    f"Failing output: {line.strip()}"
                )
        return None
