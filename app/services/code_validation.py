from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass

from .output_controls import artifact_kind


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
    """Normalize an allowed PyPI requirement for duplicate detection."""
    name = requirement.split("==", 1)[0].strip()
    return re.sub(r"[-_.]+", "-", name).lower()


_REQUIREMENT_PATTERN = re.compile(r"^[A-Za-z0-9._-]+(?:==[\w.]+)?$")
_REQUIREMENTS_HEADER = re.compile(r"^# REQUIREMENTS:\s*(.*?)\s*$", re.IGNORECASE)
_OUTPUTS_HEADER = re.compile(r"^# OUTPUTS:\s*(.*?)\s*$", re.IGNORECASE)
_BANNED_MODULES = {
    "subprocess",
    "ctypes",
    "pty",
    "socket",
    "paramiko",
    "docker",
    "pexpect",
    "requests",
    "httpx",
    "aiohttp",
    "pycurl",
    "urllib3",
}
_BANNED_NAMES = {"eval", "exec", "__import__"}
_BANNED_ATTRIBUTES = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "execv"),
    ("os", "execve"),
    ("os", "execvp"),
    ("os", "execl"),
    ("os", "execlp"),
    ("os", "execle"),
    ("os", "spawnv"),
    ("os", "spawnve"),
    ("os", "spawnl"),
    ("os", "environ"),
    ("os", "environb"),
    ("os", "getenv"),
}
_BANNED_STRINGS = (
    "/var/run/docker.sock",
    "/proc/",
    "/sys/",
    "169.254.169.254",
    "/etc/passwd",
    "/root/",
    ".ssh",
)
_CHART_MODULES = {"plotly", "seaborn"}


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

    def validate(
        self,
        code: str,
        declared_requirements: list[str],
        *,
        require_headers: bool = False,
        allowed_outputs: list[str] | None = None,
    ) -> ValidationResult:
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

        lines = code.splitlines()
        declared_output_names: list[str] = []
        if require_headers:
            requirements_match = _REQUIREMENTS_HEADER.match(lines[0]) if lines else None
            outputs_match = _OUTPUTS_HEADER.match(lines[1]) if len(lines) > 1 else None
            if not requirements_match or not outputs_match:
                return ValidationResult(
                    valid=False,
                    requirements=list(declared_requirements),
                    error=(
                        "The first two script lines must declare # REQUIREMENTS: and "
                        "# OUTPUTS: headers."
                    ),
                )
            header_requirements = requirements_match.group(1).strip()
            header_requirements_list = (
                []
                if header_requirements.lower() == "none"
                else [item.strip() for item in header_requirements.split(",") if item.strip()]
            )
            if header_requirements_list != declared_requirements:
                return ValidationResult(
                    valid=False,
                    requirements=list(declared_requirements),
                    error="# REQUIREMENTS header does not match the extracted package list.",
                )
            raw_outputs = outputs_match.group(1).strip()
            declared_output_names = (
                []
                if raw_outputs.lower() == "none"
                else [item.strip() for item in raw_outputs.split(",") if item.strip()]
            )

        declared = list(dict.fromkeys(req.strip() for req in declared_requirements if req.strip()))
        invalid_requirements = [req for req in declared if not _REQUIREMENT_PATTERN.fullmatch(req)]
        if invalid_requirements:
            return ValidationResult(
                valid=False,
                requirements=declared,
                error=(
                    "Requirements must be plain PyPI package names with an optional == version; "
                    f"invalid entries: {', '.join(invalid_requirements)}."
                ),
            )
        if len(declared) > 10:
            return ValidationResult(
                valid=False,
                requirements=declared,
                error="At most 10 third-party package requirements are allowed.",
            )

        declared_keys = {_distribution_key(req) for req in declared}
        discovered: list[str] = []
        for node in ast.walk(syntax_tree):
            if isinstance(node, ast.Import):
                module_names = [alias.name.split(".", 1)[0] for alias in node.names]
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root in _BANNED_MODULES or alias.name in {
                        "urllib.request",
                        "http.client",
                    }:
                        return ValidationResult(
                            valid=False,
                            requirements=declared,
                            error=(
                                f"Import of '{alias.name}' is not allowed by the static guard. "
                                "Use the bundled helpers for HTTP requests."
                            ),
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_names = [node.module.split(".", 1)[0]]
                root = node.module.split(".", 1)[0]
                if root in _BANNED_MODULES or node.module in {
                    "urllib.request",
                    "http.client",
                }:
                    return ValidationResult(
                        valid=False,
                        requirements=declared,
                        error=(
                            f"Import from '{node.module}' is not allowed by the static guard. "
                            "Use the bundled helpers for HTTP requests."
                        ),
                    )
                if root == "os" and any(
                    alias.name in {"*", "environ", "environb", "getenv", "system", "popen"}
                    for alias in node.names
                ):
                    return ValidationResult(
                        valid=False,
                        requirements=declared,
                        error="Environment and shell access through os imports is not allowed.",
                    )
            else:
                module_names = []

            for module in module_names:
                if module in sys.stdlib_module_names or module in {"__future__", "helpers"}:
                    continue
                distribution = IMPORT_TO_DISTRIBUTION.get(module, module)
                key = _distribution_key(distribution)
                if key not in declared_keys:
                    declared.append(distribution)
                    declared_keys.add(key)
                if module not in discovered:
                    discovered.append(module)

            if isinstance(node, ast.Call):
                function = node.func
                if isinstance(function, ast.Name) and function.id in _BANNED_NAMES:
                    return ValidationResult(
                        valid=False,
                        requirements=declared,
                        error=f"Call to {function.id}() is not allowed by the static guard.",
                    )
                if (
                    isinstance(function, ast.Attribute)
                    and isinstance(function.value, ast.Name)
                    and (function.value.id, function.attr) in _BANNED_ATTRIBUTES
                ):
                    return ValidationResult(
                        valid=False,
                        requirements=declared,
                        error=f"Call or access to {function.value.id}.{function.attr} is not allowed.",
                    )
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if (node.value.id, node.attr) in _BANNED_ATTRIBUTES:
                    return ValidationResult(
                        valid=False,
                        requirements=declared,
                        error=f"Access to {node.value.id}.{node.attr} is not allowed.",
                    )
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for banned_text in _BANNED_STRINGS:
                    if banned_text in node.value:
                        return ValidationResult(
                            valid=False,
                            requirements=declared,
                            error=f"Reference to '{banned_text}' is not allowed by the static guard.",
                        )

        used_chart_modules = _CHART_MODULES.intersection(discovered)
        if "matplotlib" in discovered:
            return ValidationResult(
                valid=False,
                requirements=declared,
                error="matplotlib is not allowed; use Plotly for requested charts.",
            )
        if used_chart_modules and (allowed_outputs is not None and "chart" not in allowed_outputs):
            return ValidationResult(
                valid=False,
                requirements=declared,
                error="Chart libraries are not allowed unless the user requested a chart.",
            )

        if require_headers:
            allowed = set(allowed_outputs or [])
            undeclared_kinds = {
                kind
                for name in declared_output_names
                if (kind := artifact_kind(name)) != "file" and kind not in allowed
            }
            if any(artifact_kind(name) == "file" for name in declared_output_names) and not allowed:
                undeclared_kinds.add("file")
            if undeclared_kinds:
                return ValidationResult(
                    valid=False,
                    requirements=declared,
                    error=(
                        "The # OUTPUTS header declares deliverables the user did not request: "
                        f"{', '.join(sorted(undeclared_kinds))}."
                    ),
                )

        if len(declared) > 10:
            return ValidationResult(
                valid=False,
                requirements=declared,
                error="At most 10 third-party packages are allowed, including inferred imports.",
            )
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
