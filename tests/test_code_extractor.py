import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import llm_service
from backend.app.utils.code_extractor import extract_code_and_requirements


def test_basic_extraction():
    llm_output = """Here is my analysis.

I'll write a Python script to accomplish this.

```python
# REQUIREMENTS: pandas, requests
import pandas as pd
import requests

print("Hello world")
```

That should do it!
"""
    code, reqs = extract_code_and_requirements(llm_output)
    assert "print" in code
    assert "pandas" in reqs
    assert "requests" in reqs
    print(f"OK: code={len(code)} chars, reqs={reqs}")


def test_no_requirements():
    llm_output = """```python
# REQUIREMENTS: none
print("Simple task")
```"""
    code, reqs = extract_code_and_requirements(llm_output)
    assert "print" in code
    assert reqs == []
    print(f"OK: no requirements handled, code={len(code)} chars")


def test_no_code_block():
    code, reqs = extract_code_and_requirements("Just some text with no code.")
    assert code == ""
    assert reqs == []
    print("OK: no code block handled")


def test_case_insensitive_requirements_tag():
    llm_output = """```python
# requirements: numpy, matplotlib
import numpy as np
import matplotlib.pyplot as plt
```"""
    code, reqs = extract_code_and_requirements(llm_output)
    assert "numpy" in reqs
    assert "matplotlib" in reqs
    print(f"OK: lowercase requirements tag handled, reqs={reqs}")


def test_multiple_code_blocks_returns_first_block():
    llm_output = """```python
# REQUIREMENTS: first, pkg
print("first")
```

```python
# REQUIREMENTS: second, pkg2
print("second")
```"""
    code, reqs = extract_code_and_requirements(llm_output)
    assert "first" in code
    assert "second" not in code
    assert "first" in reqs
    assert "pkg" in reqs
    print(f"OK: first code block selected, reqs={reqs}")


def test_ollama_qwen_coder_output():
    llm_output = """Here is the Python solution.

```python
# REQUIREMENTS: pandas, numpy
import pandas as pd
import numpy as np

result = pd.DataFrame({"value": [1, 2, 3]})
print(result.mean())
```

The script uses pandas and numpy.
"""
    code, reqs = extract_code_and_requirements(llm_output)
    assert "import pandas as pd" in code
    assert "import numpy as np" in code
    assert "pandas" in reqs
    assert "numpy" in reqs
    print(f"OK: qwen2.5-coder:7b-style output handled, reqs={reqs}")


def test_ollama_generates_output(monkeypatch):
    def fake_stream_ollama(user_prompt):
        return [
            {"message": {"content": "Here is the plan.\n"}},
            {
                "message": {
                    "content": "```python\n# REQUIREMENTS: pandas\nimport pandas as pd\nprint(\"hello\")\n```"
                }
            },
        ]

    monkeypatch.setattr(llm_service, "_stream_ollama", fake_stream_ollama)

    class FakeBus:
        def __init__(self):
            self.messages = []

        async def publish(self, message):
            self.messages.append(message)

    async def run_check():
        bus = FakeBus()
        response, code, reqs = await llm_service.generate_plan_and_code(
            "Create a simple pandas script", bus
        )
        assert "plan" in response.lower()
        assert "print" in code
        assert reqs == ["pandas"]
        assert any(item["type"] == "code" for item in bus.messages)

    asyncio.run(run_check())


def test_ollama_generates_no_output(monkeypatch):
    def fake_stream_ollama(user_prompt):
        return []

    monkeypatch.setattr(llm_service, "_stream_ollama", fake_stream_ollama)

    class FakeBus:
        async def publish(self, message):
            pass

    async def run_check():
        response, code, reqs = await llm_service.generate_plan_and_code(
            "Generate nothing", FakeBus()
        )
        assert response == ""
        assert code == ""
        assert reqs == []

    asyncio.run(run_check())


if __name__ == "__main__":
    test_basic_extraction()
    test_no_requirements()
    test_no_code_block()
    test_case_insensitive_requirements_tag()
    test_multiple_code_blocks_returns_first_block()
    test_ollama_qwen_coder_output()
    test_ollama_generates_output()
    test_ollama_generates_no_output()
    print("\nAll tests passed!")