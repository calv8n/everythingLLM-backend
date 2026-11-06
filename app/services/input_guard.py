import re
from typing import Literal

MAX_PROMPT_CHARS = 4_000

_HARD_BLOCK = re.compile(
    r"\b(keylogger|ransomware|botnet|ddos|reverse shell|credential stuffing|"
    r"phishing kit|sqlmap|masscan)\b",
    re.IGNORECASE,
)
_SUSPECT = re.compile(
    r"(\bnmap\b|port ?scan|169\.254\.169\.254|\blocalhost\b|127\.0\.0\.1|"
    r"192\.168\.|\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|"
    r"\bmine?(r|ing)?\b.*\b(bitcoin|monero|crypto)|/etc/passwd|docker\.sock|"
    r"os\.environ|environment variables|print.{0,30}\benv\b|api keys?|credentials|\.env\b)",
    re.IGNORECASE,
)


def validate_prompt(prompt: str) -> Literal["ok", "block"]:
    if len(prompt) > MAX_PROMPT_CHARS:
        return "block"
    if _HARD_BLOCK.search(prompt):
        return "block"
    return "ok"


def requires_safety_review(prompt: str) -> bool:
    return bool(_SUSPECT.search(prompt))
