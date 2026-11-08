"""Neutral demo program: echoes the input value with a tag."""
import json
import os
import sys

with open(os.environ["PROCFLOW_INPUTS"], encoding="utf-8") as fh:
    data = json.load(fh)
tag = sys.argv[1] if len(sys.argv) > 1 else "x"
text = "" if tag == "empty" else f"{tag}:{data['inputs']['value']}"
with open(os.environ["PROCFLOW_OUTPUTS"], "w", encoding="utf-8") as fh:
    json.dump({"echoed": text}, fh)
