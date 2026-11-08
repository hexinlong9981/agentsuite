import json
import os
import sys

with open(os.environ["PROCFLOW_OUTPUTS"], "w", encoding="utf-8") as fh:
    json.dump({"kind": sys.argv[1]}, fh)
