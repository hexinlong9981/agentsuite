"""Executable entry point for python -m procflow."""
import sys
from .cli import main

if __name__ == "__main__":
    main(sys.argv[1:])
