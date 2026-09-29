"""
CCPM Registry Entry Point

Runs the registry CLI from the repository root, as in `uv run main.py -h`.
"""

# MARK: Imports
import sys

from ccpm_registry.cli import main

# MARK: Execution
if __name__ == "__main__":
    sys.exit(main())
