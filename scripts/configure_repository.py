#!/usr/bin/env python3
"""Fill GitHub-specific HACS metadata once before publishing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "barista_assist" / "manifest.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("owner", help="GitHub username or organisation")
    parser.add_argument("repo", nargs="?", default="barista-assist")
    args = parser.parse_args()

    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    base = f"https://github.com/{args.owner}/{args.repo}"
    data["documentation"] = f"{base}#readme"
    data["issue_tracker"] = f"{base}/issues"
    data["codeowners"] = [f"@{args.owner}"]
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {MANIFEST.relative_to(ROOT)} for {base}")


if __name__ == "__main__":
    main()
