from __future__ import annotations

import json
import sys
from pathlib import Path
from html.parser import HTMLParser


REQUIRED_FILES = ["index.html", "styles.css", "app.js", "codebase-map.json"]
REQUIRED_JSON_KEYS = [
    "system",
    "components",
    "flows",
    "states",
    "integrations",
    "risks",
    "tests",
    "codeMap",
    "sources",
]
REQUIRED_HTML_TERMS = [
    "Overview",
    "Runtime",
    "Components",
    "States",
    "Integrations",
    "Risks",
    "Code",
]


class RefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("href"):
            self.refs.append(attrs["href"])
        if tag == "script" and attrs.get("src"):
            self.refs.append(attrs["src"])


def fail(message: str) -> None:
    print(f"ERROR: {message}")
    raise SystemExit(1)


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: validate_dashboard.py <dashboard-directory>")

    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        fail(f"dashboard directory does not exist: {root}")

    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    if missing:
        fail(f"missing required files: {', '.join(missing)}")

    data_path = root / "codebase-map.json"
    try:
        data = json.loads(data_path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"invalid codebase-map.json: {exc}")

    missing_keys = [key for key in REQUIRED_JSON_KEYS if key not in data]
    if missing_keys:
        fail(f"codebase-map.json missing keys: {', '.join(missing_keys)}")

    html = (root / "index.html").read_text(encoding="utf-8")
    lower = html.lower()

    if "<html" not in lower or "<body" not in lower:
        fail("index.html is not a complete HTML document")

    parser = RefParser()
    parser.feed(html)
    for ref in parser.refs:
        if ref.startswith(("http://", "https://", "//", "data:")):
            continue
        path = (root / ref.split("?", 1)[0].split("#", 1)[0]).resolve()
        if not path.exists():
            fail(f"broken local asset reference: {ref}")

    combined = html + "\n" + (root / "app.js").read_text(encoding="utf-8")
    missing_terms = [term for term in REQUIRED_HTML_TERMS if term.lower() not in combined.lower()]
    if missing_terms:
        fail(f"dashboard may be missing major sections: {', '.join(missing_terms)}")

    md_markers = combined.count("```")
    if md_markers > 2:
        fail("dashboard appears to contain a raw Markdown dump")

    if len(data.get("components", [])) == 0:
        fail("components is empty")
    if len(data.get("flows", [])) == 0:
        fail("flows is empty")
    if len(data.get("sources", [])) == 0:
        fail("sources is empty")

    print("OK: dashboard structure validated")


if __name__ == "__main__":
    main()
