"""Run dependency-free checks without contacting Minecraft."""
import ast
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
for folder in ('server', 'scripts', 'tests', 'map'):
    for path in (ROOT / folder).glob('*.py'):
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
subprocess.run([sys.executable, str(ROOT / 'tests/test_chat.py')], check=True)
subprocess.run([sys.executable, str(ROOT / 'tests/test_deploy.py')], check=True)
subprocess.run([sys.executable, str(ROOT / 'tests/test_map_adapter.py')], check=True)
subprocess.run([sys.executable, str(ROOT / 'tests/test_map_defaults.py')], check=True)
subprocess.run([sys.executable, str(ROOT / 'docs/bluemap-26.3/normalize.py')], check=True)
print('Python syntax, mocked chat, and isolated deployment checks passed.')
