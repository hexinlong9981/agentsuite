"""Neutral demo program: reads the inputs file, writes a result to the outputs file, prints to stdout."""
import json
import os
import sys

with open(os.environ["PROCFLOW_INPUTS"], encoding="utf-8") as fh:
    data = json.load(fh)
text = " ".join(sys.argv[1:]) or "ok"
out = {"result": text, "summary": text, "undeclared": 1}
with open(os.environ["PROCFLOW_OUTPUTS"], "w", encoding="utf-8") as fh:
    json.dump(out, fh)
print("hello from emit; inputs keys:", ",".join(sorted(data.get("inputs", {}))))
