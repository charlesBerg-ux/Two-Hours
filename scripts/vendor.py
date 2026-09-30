#!/usr/bin/env python3
"""Copy scripts/twohours.py into every skill's scripts/ folder.

Skills are often installed one folder at a time, so each carries its own copy
of the data script. This file keeps those copies identical to the source.
The test suite fails if any copy is out of date.

    python3 scripts/vendor.py
"""

import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "scripts", "twohours.py")
SKILLS = os.path.join(ROOT, "skills")


def main():
    if not os.path.isdir(SKILLS):
        print("No skills folder yet.")
        return
    for name in sorted(os.listdir(SKILLS)):
        skill = os.path.join(SKILLS, name)
        if not os.path.exists(os.path.join(skill, "SKILL.md")):
            continue
        target_dir = os.path.join(skill, "scripts")
        os.makedirs(target_dir, exist_ok=True)
        shutil.copyfile(SOURCE, os.path.join(target_dir, "twohours.py"))
        print(f"updated skills/{name}/scripts/twohours.py")


if __name__ == "__main__":
    main()
