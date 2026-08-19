"""Reproduce research.md R2's table: docdoc's dialect against CPython's `re`.

    uv run python tests/perf/bench_pattern_dialect.py

Not a test — it takes seconds and its numbers move with the machine. It exists so
that the decision to write an engine can be re-checked rather than believed, and
so the numbers in the plan can be regenerated when someone doubts them.

The `google-re2` column of R2's table is deliberately absent: that package is not
a dependency, and the measurement exists to record why it is not.
"""

from __future__ import annotations

import re
import time

from docdoc.validation.pattern import compile_pattern

ADVERSARIAL = r"(a+)+"
TYPICAL = r"INV-\d{4}-[A-Z]{2}"


def _best(call, runs: int = 3) -> float:
    best = float("inf")
    for _ in range(runs):
        started = time.perf_counter()
        call()
        best = min(best, time.perf_counter() - started)
    return best


def main() -> None:
    print(f"{'case':<44}{'docdoc':>12}{'stdlib re':>14}")
    print("-" * 70)

    mine = compile_pattern(ADVERSARIAL)
    theirs = re.compile(ADVERSARIAL)
    for size in (18, 20, 22, 24):
        text = "a" * size + "!"
        ours = _best(lambda t=text: mine.fullmatch(t))
        stdlib = _best(lambda t=text: theirs.fullmatch(t))
        label = f"{ADVERSARIAL!r} vs {size} chars"
        print(f"{label:<44}{ours * 1000:>10.2f} ms{stdlib * 1000:>11.2f} ms")

    for size in (1_000, 10_000):
        text = "a" * size + "!"
        ours = _best(lambda t=text: mine.fullmatch(t))
        label = f"{ADVERSARIAL!r} vs {size} chars"
        print(f"{label:<44}{ours * 1000:>10.2f} ms{'(does not finish)':>14}")

    mine_typical = compile_pattern(TYPICAL)
    theirs_typical = re.compile(TYPICAL)
    runs = 20_000
    ours = _best(lambda: [mine_typical.fullmatch("INV-2026-VN") for _ in range(runs)])
    stdlib = _best(lambda: [theirs_typical.fullmatch("INV-2026-VN") for _ in range(runs)])
    print(
        f"{'typical value, per call':<44}"
        f"{ours / runs * 1e6:>10.2f} us{stdlib / runs * 1e6:>11.2f} us"
    )

    print(
        "\nThe first block is the decision: at 24 characters the stdlib engine already "
        "\nneeds about a second, and it doubles per character. The last line is the price "
        "\npaid for that guarantee on ordinary input."
    )


if __name__ == "__main__":
    main()
