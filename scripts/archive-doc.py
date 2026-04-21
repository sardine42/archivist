#!/usr/bin/env python3
"""归档 Markdown 文档。封装 archivist doc add。"""
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

args = ["python3", "-m", "archivist.cli", "doc", "add"] + sys.argv[1:]
env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")}
sys.exit(subprocess.call(args, cwd=str(PROJECT_ROOT), env=env))
