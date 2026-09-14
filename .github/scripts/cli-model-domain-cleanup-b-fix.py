from pathlib import Path
import re

path = Path("tests/agent/test_onboard_logic.py")
text = path.read_text()
text = re.sub(
    r'ModelConfig\(\n\s+display_name="Test", provider="openai", model=("[^"]+"),\n\s+provider=("[^"]+"),\n\s+\)',
    r'ModelConfig(\n            display_name="Test", provider=\2, model=\1,\n        )',
    text,
)
path.write_text(text)
