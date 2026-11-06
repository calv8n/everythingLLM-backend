import re

MAX_STDOUT_CHARS = 200_000
MAX_ARTIFACT_BYTES = 5_000_000
MAX_ARTIFACTS = 5

_SECRET_PATTERN = re.compile(
    r"(gsk_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
_RESULT_PATTERN = re.compile(r"\[\[RESULT\]\](.*?)\[\[/RESULT\]\]", re.DOTALL)
_ARTIFACT_KINDS = {
    "chart": (".html", ".png", ".svg"),
    "csv": (".csv", ".tsv", ".xlsx"),
    "report": (".pdf", ".md", ".docx", ".txt"),
}
_DELIVERABLE_PATTERNS = {
    "chart": re.compile(
        r"\b(chart|plot|graph|visuali[sz]\w*|histogram|heatmap|dashboard)\b",
        re.IGNORECASE,
    ),
    "csv": re.compile(
        r"\b(csv|spreadsheet|excel|xlsx|export|download)\b",
        re.IGNORECASE,
    ),
    "report": re.compile(r"\b(report|pdf|document)\b", re.IGNORECASE),
}


def clean_output(text: str) -> str:
    limited = text[:MAX_STDOUT_CHARS]
    return _SECRET_PATTERN.sub("[REDACTED]", limited)


def extract_result(stdout: str) -> str:
    match = _RESULT_PATTERN.search(stdout)
    if match:
        return match.group(1).strip()
    tail = "\n".join(stdout.strip().splitlines()[-40:])
    return tail or "Task completed successfully."


def requested_deliverables(prompt: str) -> list[str]:
    return [
        kind
        for kind, pattern in _DELIVERABLE_PATTERNS.items()
        if pattern.search(prompt)
    ]


def artifact_kind(name: str) -> str:
    lower_name = name.lower()
    for kind, extensions in _ARTIFACT_KINDS.items():
        if lower_name.endswith(extensions):
            return kind
    return "file"


def gate_artifacts(
    artifacts: list[dict],
    allowed: list[str],
) -> tuple[list[dict], list[str]]:
    allowed_kinds = set(allowed)
    kept: list[dict] = []
    dropped: list[str] = []

    for artifact in artifacts:
        name = str(artifact.get("name", "file"))
        size = artifact.get("size", 0)
        if not isinstance(size, int) or size < 0 or size > MAX_ARTIFACT_BYTES:
            dropped.append(name)
            continue
        kind = artifact_kind(name)
        if kind in allowed_kinds or (kind == "file" and allowed_kinds):
            kept.append(artifact)
        else:
            dropped.append(name)

    if len(kept) > MAX_ARTIFACTS:
        dropped.extend(str(item.get("name", "file")) for item in kept[MAX_ARTIFACTS:])
        kept = kept[:MAX_ARTIFACTS]
    return kept, dropped
