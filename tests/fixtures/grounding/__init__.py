"""Documents whose typesetting is the reason ADR-0006 exists.

These are text constants rather than PDFs on purpose. What the match view has to
survive is a property of the *characters* a parser produced -- a ligature, a soft
hyphen, a hyphen at a line break -- not of how a page was rendered. Round-tripping
them through a PDF would add a parser's behaviour to a test about folding rules,
and would make the fixture unreadable in review.

Every string here is synthetic. See tests/fixtures/make_fixtures.py for the same
rule applied to the binary fixtures.
"""

from __future__ import annotations

__all__ = [
    "ADVERSARIAL_CLAIMS",
    "ADVERSARIAL_TEXT",
    "COMBINING_MARK",
    "COMPOUND_BROKEN",
    "EXOTIC_SPACES",
    "IDENTIFIER_BROKEN",
    "LIGATURE",
    "LOWERCASE_BROKEN",
    "SOFT_HYPHEN",
    "TYPESETTING_CASES",
]

#: U+FB01 LATIN SMALL LIGATURE FI. NFKC expands it to "fi", so a model quoting
#: what a human reads still matches at the exact tier.
LIGATURE = "Invoice ﬁnal total 1,240.00"

#: U+00AD SOFT HYPHEN. NFKC leaves it alone -- measured, research.md R5 -- so the
#: view removes it explicitly. Without that rule this claim cannot match.
SOFT_HYPHEN = "Payment due for in­voice INV-001"

#: A lowercase word broken by a line-break hyphen. Both sides lowercase, so the
#: rule joins it: "am-\nount" -> "amount".
LOWERCASE_BROKEN = "Total am-\nount payable 1,240.00"

#: An identifier broken by a line-break hyphen. The character before the hyphen
#: is uppercase, so the hyphen is KEPT and only the break is removed. Joining it
#: would score 0.833 against the model's quote and fall below threshold.
IDENTIFIER_BROKEN = "Reference INV-\n2024-001 approved"

#: A genuine compound word broken at a line end. Both sides lowercase, so the
#: rule joins it to "wellknown" and the claim "well-known" then scores exactly
#: 0.900 -- clearing the threshold by nothing at all. This is the residual loss
#: research.md R7 accepted, and the tripwire for any future threshold increase.
COMPOUND_BROKEN = "A well-\nknown supplier of parts"

#: U+00A0 NBSP, U+202F narrow NBSP, U+2007 figure space. NFKC folds all three to
#: U+0020, so no separate rule is needed -- measured, research.md R5.
EXOTIC_SPACES = "Amount due 1,240.00 EUR"

#: "e" + U+0301 COMBINING ACUTE composes to a single character under NFKC. This
#: is the case that proves the offset map cannot be arithmetic: the view is
#: SHORTER than the source here, while the ligature case makes it LONGER.
COMBINING_MARK = "Société Générale reference"

#: Every typesetting case, with the claim a model would plausibly return for it
#: and whether that claim should reach the exact tier once folded.
TYPESETTING_CASES: tuple[tuple[str, str, str, bool], ...] = (
    ("ligature", LIGATURE, "Invoice final total 1,240.00", True),
    ("soft_hyphen", SOFT_HYPHEN, "invoice INV-001", True),
    ("lowercase_broken", LOWERCASE_BROKEN, "Total amount payable 1,240.00", True),
    ("identifier_broken", IDENTIFIER_BROKEN, "INV-2024-001", True),
    ("exotic_spaces", EXOTIC_SPACES, "Amount due 1,240.00 EUR", True),
    ("combining_mark", COMBINING_MARK, "Société Générale", True),
    # The residual loss of research.md R7: joined to "wellknown", so the claim
    # does NOT match exactly. It resolves at the fuzzy tier, at exactly 0.900.
    ("compound_broken", COMPOUND_BROKEN, "well-known", False),
)

#: A deliberately adversarial document: one 28-character string repeated 2,000
#: times, 56,000 characters. The claims below do NOT appear verbatim, so they
#: fall through to the fuzzy tier, where their blocks are near-ubiquitous. This
#: is the input the candidate budget exists for (research.md R8).
ADVERSARIAL_TEXT = "Invoice Total Amount Due    " * 2_000

#: Measured unbounded on a contributor laptop: 3 ms, 53 ms, and 139 ms
#: respectively. Twenty values of the third shape would take 2.8 s and blow
#: SC-020 by 5.6x, which is what sized the default candidate budget.
ADVERSARIAL_CLAIMS: tuple[str, ...] = (
    "Totxl",
    "Total Amount Dux",
    "Xnvoice Total Amount Due",
)
