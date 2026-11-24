"""Print the real-OrcaSlicer self-test report and fail when a step failed.

    python scripts/e2e_check.py report.json
"""

import json
import sys


def main(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as error:
        print("::error::no self-test report (%s): the plugin did not load or OrcaSlicer did not start" % error)
        return 1
    print("FilaForge %s in OrcaSlicer; orca module: %s" % (data.get("filaforge"), ", ".join(data.get("orca_module", []))))
    for step in data["steps"]:
        mark = "ok  " if step["ok"] else "FAIL"
        print("%s %-28s %6ss  %s" % (mark, step["name"], step.get("seconds", ""), step.get("detail", "")))
        if not step["ok"]:
            print("::error title=%s::%s" % (step["name"], step.get("detail", "")))
            if step.get("trace"):
                print(step["trace"])
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
