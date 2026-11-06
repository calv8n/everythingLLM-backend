from backend.app.services.code_validation import ValidationAgent


def test_validator_adds_unlisted_third_party_imports():
    result = ValidationAgent().validate(
        "import pandas as pd\nimport plotly.express as px",
        [],
        allowed_outputs=["chart"],
    )

    assert result.valid
    assert result.requirements == ["pandas", "plotly"]
    assert set(result.discovered_imports) == {"pandas", "plotly"}


def test_validator_maps_import_names_to_pip_distribution_names():
    result = ValidationAgent().validate("from bs4 import BeautifulSoup\nimport yaml", [])

    assert result.valid
    assert result.requirements == ["beautifulsoup4", "PyYAML"]


def test_validator_treats_bundled_helpers_as_local_code():
    result = ValidationAgent().validate("from helpers import get_json", [])

    assert result.valid
    assert result.requirements == []
    assert result.discovered_imports == ()


def test_validator_rejects_syntax_errors_before_execution():
    result = ValidationAgent().validate("print(items[", ["requests"])

    assert not result.valid
    assert "syntax validation" in (result.error or "")
    assert result.requirements == ["requests"]


def test_execution_output_validator_detects_caught_json_and_fetch_errors():
    validator = ValidationAgent()
    output = (
        "Error fetching data for IND: Expecting value: line 1 column 1 (char 0)\n"
        "Failed to fetch GDP data for one or both countries."
    )

    diagnostic = validator.validate_execution_output(output)

    assert diagnostic is not None
    assert "Error fetching data for IND" in diagnostic


def test_execution_output_validator_accepts_normal_output_and_warnings():
    validator = ValidationAgent()

    assert validator.validate_execution_output(
        "WARNING: running in a container\nFetched 2 countries successfully\nGDP table saved"
    ) is None


def test_static_guard_rejects_banned_modules_calls_and_sensitive_paths():
    validator = ValidationAgent()

    for code in (
        "import subprocess",
        "import requests",
        "from urllib.request import urlopen",
        "import socket",
        "eval('1 + 1')",
        "import os\nos.environ",
        "import os\nos.getenv('GROQ_API_KEY')",
        "from os import *",
        "path = '/var/run/docker.sock'",
    ):
        result = validator.validate(code, [])
        assert not result.valid
        assert "static guard" in (result.error or "").lower() or "not allowed" in (result.error or "").lower()


def test_requirements_reject_urls_and_limit_package_count():
    validator = ValidationAgent()

    assert not validator.validate("print('ok')", ["git+https://example.com/pkg.git"]).valid
    assert not validator.validate(
        "print('ok')",
        [f"package{i}" for i in range(11)],
    ).valid


def test_execution_contract_requires_first_two_headers_and_allowed_outputs():
    validator = ValidationAgent()
    code = "# REQUIREMENTS: none\n# OUTPUTS: chart.html\nprint('ok')"

    missing = validator.validate("print('ok')", [], require_headers=True, allowed_outputs=[])
    denied = validator.validate(code, [], require_headers=True, allowed_outputs=[])
    allowed = validator.validate(code, [], require_headers=True, allowed_outputs=["chart"])

    assert not missing.valid
    assert not denied.valid
    assert allowed.valid


def test_chart_import_is_rejected_when_not_requested():
    result = ValidationAgent().validate(
        "import plotly.express as px",
        [],
        allowed_outputs=[],
    )

    assert not result.valid
    assert "Chart libraries" in (result.error or "")