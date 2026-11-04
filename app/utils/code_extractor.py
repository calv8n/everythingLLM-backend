import re


def extract_code_and_requirements(llm_response: str) -> tuple[str, list[str]]:
    """Extract a code block and any declared requirements from an LLM response."""
    pattern = r"```(?:python)?\s*\n(.*?)```"
    match = re.search(pattern, llm_response, re.DOTALL | re.IGNORECASE)

    if not match:
        return "", []

    code = match.group(1).strip()

    req_match = re.search(
        r"^\s*#\s*REQUIREMENTS:\s*(.*?)\s*$",
        code,
        re.IGNORECASE | re.MULTILINE,
    )
    requirements: list[str] = []
    if req_match:
        raw = req_match.group(1).strip()
        if raw.lower() != "none":
            requirements = [req.strip() for req in raw.split(",") if req.strip()]

    return code, requirements