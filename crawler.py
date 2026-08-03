#!/usr/bin/env python3
"""Compatibility entry point for the NEXO crawler CLI."""

import sys
from pathlib import Path


PROJECT_SRC = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(PROJECT_SRC))

from nexo_crawler.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
