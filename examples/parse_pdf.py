"""Parse a real PDF and point at where a value came from.

Needs ``docdoc[pdf]`` and nothing else -- no credentials, no network, no database.

    uv run python examples/parse_pdf.py tests/fixtures/pdf/digital_invoice.pdf

Note that no provider is named anywhere below. The caller asks for the
capabilities it needs; which parser supplies them is a deployment's choice.
"""

from __future__ import annotations

import sys
from pathlib import Path

from docdoc.ingest import CapabilityRequest, parse


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    path = Path(argv[1])
    document = parse(
        path.read_bytes(),
        require=CapabilityRequest(media_type="application/pdf", geometry=True),
    )

    print(f"{path.name}: {len(document.pages)} page(s), {len(list(document.tokens))} tokens")
    print(f"  parser      {document.provenance.parser_id} {document.provenance.parser_version}")
    print(f"  reading     {document.provenance.reading_order}")
    print(f"  text layer  used={document.provenance.text_layer_used}")

    verdict = document.provenance.text_layer
    if verdict is not None:
        print(f"  rule        {verdict.rule_id}")
        for page in verdict.pages:
            state = "text" if page.text_bearing else "no text"
            print(f"    page {page.page_index + 1}: {page.char_count} chars ({state})")

    # Find a value and ask where it physically sits. This is the whole point of
    # the kernel being reachable from a file.
    needle = "INV-"
    matches = document.find(needle)
    if not matches:
        print(f'  no occurrence of "{needle}" in this document')
        return 0

    span = matches[0]
    print(f'  found "{document.text[span.start : span.end]}" at {span.start}:{span.end}')
    for geometry in document.locate(span):
        box = geometry.bbox
        print(
            f"    page {geometry.page_index + 1} "
            f"box ({box.x0:.3f}, {box.y0:.3f}) -> ({box.x1:.3f}, {box.y1:.3f})"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
