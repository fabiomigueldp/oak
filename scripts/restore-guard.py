"""Prevent Java from opening a world left halfway through a restore."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admin.runtime import Runtime

if __name__ == '__main__':
    Runtime().guard_start()
