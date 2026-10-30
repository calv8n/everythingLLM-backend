from backend.app.services.code_validation import ValidationAgent


def test_validator_adds_unlisted_third_party_imports():
    result = ValidationAgent().validate(
        "import requests\nimport pandas as pd\nimport plotly.express as px",
        [],
    )

    assert result.valid
    assert result.requirements == ["requests", "pandas", "plotly"]
    assert set(result.discovered_imports) == {"requests", "pandas", "plotly"}


def test_validator_maps_import_names_to_pip_distribution_names():
    result = ValidationAgent().validate("from bs4 import BeautifulSoup\nimport yaml", [])

    assert result.valid
    assert result.requirements == ["beautifulsoup4", "PyYAML"]


def test_validator_keeps_declared_versions_without_duplicate_install():
    result = ValidationAgent().validate("import requests", ["requests>=2.31"])

    assert result.valid
    assert result.requirements == ["requests>=2.31"]


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