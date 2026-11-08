"""Neutral demo program: asks the engine to raise the exception type given as the first argument."""
import json
import os
import sys

with open(os.environ["PROCFLOW_OUTPUTS"], "w", encoding="utf-8") as fh:
    json.dump({"__raise__": sys.argv[1]}, fh)
