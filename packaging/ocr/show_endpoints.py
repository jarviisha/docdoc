"""Print an OCR endpoints file with its secrets hidden, and probe each endpoint.

`make ocr` runs this against `DOCDOC_OCR_ENDPOINTS_FILE`. Header values are
never printed: the file holds the bearer tokens the OCR hosts check.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


def health(url: str, headers: dict[str, str]) -> str:
    """`GET <base>/health` with the endpoint's own headers: the status, or why not."""
    probe = url.rsplit("/", 1)[0] + "/health"
    try:
        request = urllib.request.Request(probe, headers=headers)
        with urllib.request.urlopen(request, timeout=10) as response:
            return str(response.status)
    except urllib.error.HTTPError as error:
        return str(error.code)
    except OSError as error:
        return f"unreachable ({type(error).__name__})"


def main(path: str) -> int:
    with open(path, encoding="utf-8") as handle:
        endpoints = json.load(handle)["endpoints"]
    print(f"endpoints file: {path}")
    for e in endpoints:
        headers = e.get("headers", {})
        print(
            f"  {e['id']:<10} {e['url']}\n"
            f"             version={e.get('version')}  languages={','.join(e.get('languages', []))}"
            f"  max_pages={e.get('max_pages', '-')}  timeout={e.get('timeout_s', '-')}s"
            f"  deadline={e.get('deadline_s', '-')}s\n"
            f"             headers={','.join(sorted(headers)) or '-'} (values hidden)"
            f"  health={health(e['url'], headers)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
