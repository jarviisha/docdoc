"""``pattern_dialect@1``: a documented regular-expression subset that cannot hang.

**Why this module exists at all**, because writing a regular-expression engine is
exactly the thing Principle XI warns against, and "we needed one" is not an
argument on its own.

CPython's ``re`` is a backtracking engine. Measured on the pattern ``^(a+)+$``
against ``"a" * n + "!"``:

    n=18  18.2 ms · n=20  73.2 ms · n=22  296.1 ms · n=24  **1,183.5 ms**

It doubles per character. Field values are routinely 30-60 characters, so a
length precondition does not rescue it -- at 40 characters that pattern runs for
days. The obvious remedy, a timeout, is worse than the disease: it would make a
verdict depend on how fast the machine was, so one artifact id could describe two
different answers, which Principle III forbids and no identity could describe.

Two sound options were measured. ``google-re2`` is linear and about 1.45x faster
than this module on typical values (6.08 us against 8.84 us). It was declined for
a correctness reason before a packaging one: with RE2 the *dialect* is whatever
the installed binary implements, so the engine's version would have to be folded
into ``options_hash``, and two machines with different wheels could produce
different verdicts. Owning the dialect makes ``PATTERN_DIALECT_VERSION`` a docdoc
constant that a bump can protect. (The packaging reason is real too: it is a
native C++ extension bundling abseil, with no musl wheels.)

**What contains the risk.** The subset below is small and closed; anything
outside it is rejected when the schema loads rather than at verdict time; and
``tests/property/test_pattern_dialect.py`` generates patterns inside the subset
and asserts this engine and ``re.fullmatch`` return the same answer. The stdlib
is the oracle for *what* matches. This engine exists only for *how long it may
take*.

**The dialect.** Literals; ``.``; character classes with ranges and negation; the
escapes ``\\d \\D \\w \\W \\s \\S`` and any escaped metacharacter; groups;
alternation; ``*``, ``+``, ``?``; and counted repetition ``{m}``, ``{m,}``,
``{m,n}``. A leading ``^`` and a trailing ``$`` are accepted and redundant,
because a pattern constraint matches the whole value anyway. Backreferences,
lookaround, named groups, and inline flags are **not** in the dialect.
"""

from __future__ import annotations

from collections.abc import Callable

__all__ = [
    "MAX_NODES",
    "MAX_REPEAT",
    "PATTERN_DIALECT_VERSION",
    "PatternSyntaxError",
    "compile_pattern",
]

#: Designates the **whole** dialect: the accepted syntax, the semantics of each
#: construct, whole-value matching, and the two limits below. Changing any of
#: them changes what a `pattern` constraint means, and therefore changes
#: verdicts, so it REQUIRES a bump (VAL-30).
PATTERN_DIALECT_VERSION = "pattern_dialect@1"

#: The largest repetition count a pattern may name. `\\d{5000}` is not a
#: validation rule anyone means; it is a way to build a large automaton.
MAX_REPEAT = 1000

#: The automaton size ceiling, checked while building. Counted repetition
#: expands, so `(\\d{100}){100}` is small to write and large to build -- this is
#: what stops the expansion rather than trusting authors not to write one.
MAX_NODES = 20_000


class PatternSyntaxError(ValueError):
    """A pattern outside ``pattern_dialect@1``.

    A ``ValueError`` so that the schema layer's existing load-time handling
    turns it into a ``SchemaError`` naming the field, rather than needing a
    second error path for the same class of authoring mistake.
    """


# --------------------------------------------------------------------------
# Character predicates
# --------------------------------------------------------------------------

Predicate = Callable[[str], bool]

_CLASS_ESCAPES: dict[str, Predicate] = {
    "d": str.isdigit,
    "D": lambda ch: not ch.isdigit(),
    "w": lambda ch: ch.isalnum() or ch == "_",
    "W": lambda ch: not (ch.isalnum() or ch == "_"),
    "s": str.isspace,
    "S": lambda ch: not ch.isspace(),
}

#: Escapes that stand for a literal control character rather than a class.
_LITERAL_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "f": "\f", "v": "\v", "0": "\0"}

#: Perl constructs the dialect deliberately does not implement. Named so the
#: load-time error can say *which* one was used rather than "syntax error".
_UNSUPPORTED = {
    "(?=": "a lookahead",
    "(?!": "a negative lookahead",
    "(?<=": "a lookbehind",
    "(?<!": "a negative lookbehind",
    "(?P": "a named group",
    "(?i": "an inline flag",
    "(?m": "an inline flag",
    "(?s": "an inline flag",
    "(?x": "an inline flag",
    "(?#": "a comment group",
}


# --------------------------------------------------------------------------
# The automaton
# --------------------------------------------------------------------------


def _literal(expected: str) -> Predicate:
    """Match exactly one character.

    A named factory rather than an inline lambda with a default argument: the
    default-argument trick is the usual way to capture a loop variable, and it
    defeats type inference, so this says the same thing in a form a reader and a
    checker can both follow.
    """
    return lambda ch: ch == expected


def _range(low: str, high: str) -> Predicate:
    """Match one character inside an inclusive range."""
    return lambda ch: low <= ch <= high


class _Node:
    """One NFA state. ``kind`` is ``"char"``, ``"split"``, or ``"match"``."""

    __slots__ = ("kind", "out", "out2", "predicate")

    def __init__(self, kind: str, predicate: Predicate | None = None) -> None:
        self.kind = kind
        self.predicate = predicate
        self.out: _Node | None = None
        self.out2: _Node | None = None


class _Fragment:
    """A partially built automaton: an entry state and the exits still dangling.

    Exits are recorded as ``(node, slot)`` pairs rather than as closures. An
    earlier draft used closures and they made the fragment unprintable and the
    bug that followed unreadable; a pair of a node and which of its two pointers
    is dangling is the same information a debugger can show.
    """

    __slots__ = ("exits", "start")

    def __init__(self, start: _Node, exits: list[tuple[_Node, int]]) -> None:
        self.start = start
        self.exits = exits

    def patch(self, target: _Node) -> None:
        for node, slot in self.exits:
            if slot == 1:
                node.out = target
            else:
                node.out2 = target


class Pattern:
    """A compiled pattern. Matching is linear in the length of the value.

    Built once when the schema loads and reused for every value the constraint
    applies to, which is why compilation may be the expensive half.
    """

    __slots__ = ("_size", "_start", "source")

    def __init__(self, source: str, start: _Node, size: int) -> None:
        self.source = source
        self._start = start
        self._size = size

    @property
    def node_count(self) -> int:
        return self._size

    def fullmatch(self, text: str) -> bool:
        """Whether the **whole** value is in the language (FR-024).

        A substring match would make ``pattern: "[0-9]{4}"`` accept any string
        containing four digits, which is almost never what an author means and
        fails in the permissive direction -- the direction that produces a
        verdict of `valid` for a document nobody checked.

        The simulation carries a set of live states forward one character at a
        time. It never backtracks, so its cost is ``O(len(text) * states)`` and
        no pattern in the dialect can make it super-linear.
        """
        current: list[_Node] = []
        self._add(self._start, current, set())
        for char in text:
            if not current:
                return False
            following: list[_Node] = []
            seen: set[int] = set()
            for node in current:
                if node.kind == "char" and node.predicate is not None and node.predicate(char):
                    self._add(node.out, following, seen)
            current = following
        return any(node.kind == "match" for node in current)

    @staticmethod
    def _add(node: _Node | None, states: list[_Node], seen: set[int]) -> None:
        """Follow empty transitions to the states that can consume a character.

        Iterative rather than recursive: a pattern like ``(a?){900}`` produces a
        long chain of splits, and recursion here would trade a hang for a
        ``RecursionError`` -- a different way to fail on adversarial input, not a
        fix.
        """
        stack = [node]
        while stack:
            item = stack.pop()
            if item is None or id(item) in seen:
                continue
            seen.add(id(item))
            if item.kind == "split":
                stack.append(item.out2)
                stack.append(item.out)
            else:
                states.append(item)


# --------------------------------------------------------------------------
# The parser
# --------------------------------------------------------------------------


class _Parser:
    """Recursive descent over the dialect, building the automaton as it goes."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.index = 0
        self.count = 0

    # -- helpers ---------------------------------------------------------

    def node(self, kind: str, predicate: Predicate | None = None) -> _Node:
        self.count += 1
        if self.count > MAX_NODES:
            raise PatternSyntaxError(
                f"pattern expands past {MAX_NODES} automaton states. Counted "
                "repetition is expanded, so a nested one can be small to write and "
                "very large to build"
            )
        return _Node(kind, predicate)

    def peek(self) -> str:
        return self.source[self.index] if self.index < len(self.source) else ""

    def fail(self, message: str) -> PatternSyntaxError:
        return PatternSyntaxError(f"{message} at position {self.index} of {self.source!r}")

    # -- grammar ---------------------------------------------------------

    def parse(self) -> _Node:
        fragment = self.alternation()
        if self.index != len(self.source):
            raise self.fail(f"unexpected {self.peek()!r}")
        match_node = self.node("match")
        fragment.patch(match_node)
        return fragment.start

    def alternation(self) -> _Fragment:
        left = self.concatenation()
        while self.peek() == "|":
            self.index += 1
            right = self.concatenation()
            split = self.node("split")
            split.out = left.start
            split.out2 = right.start
            left = _Fragment(split, left.exits + right.exits)
        return left

    def concatenation(self) -> _Fragment:
        parts: list[_Fragment] = []
        while self.peek() not in ("", "|", ")"):
            parts.append(self.repetition())
        if not parts:
            return self.empty()
        result = parts[0]
        for part in parts[1:]:
            result.patch(part.start)
            result = _Fragment(result.start, part.exits)
        return result

    def empty(self) -> _Fragment:
        """The empty language element -- one split with both exits dangling."""
        split = self.node("split")
        return _Fragment(split, [(split, 1), (split, 2)])

    def repetition(self) -> _Fragment:
        start = self.index
        atom = self.atom()
        atom_source = self.source[start : self.index]
        char = self.peek()
        if char in ("*", "+", "?"):
            self.index += 1
            atom = self.quantify(atom, char)
        elif char == "{" and self._looks_like_a_count():
            low, high = self.counted()
            atom = self.repeat(atom_source, low, high, atom)
        else:
            return atom
        self._reject_stacked_quantifier()
        return atom

    def _reject_stacked_quantifier(self) -> None:
        """``a+?`` and ``a+{2}`` are refused rather than guessed at.

        ``a+?`` is a lazy quantifier in Perl syntax, and laziness cannot change
        *whether* a whole value matches -- only which sub-match a capturing engine
        would report, and this dialect has no captures. Silently accepting it as
        "``+`` then optional" would give an author a pattern that means something
        different here than everywhere else they have seen it.
        """
        char = self.peek()
        if char in ("*", "+", "?") or (char == "{" and self._looks_like_a_count()):
            raise PatternSyntaxError(
                f"a quantifier is applied to a quantifier at position {self.index} of "
                f"{self.source!r}. Lazy quantifiers are not part of "
                f"{PATTERN_DIALECT_VERSION}; to repeat a repetition, group it: '(a+){{2}}'"
            )

    def _looks_like_a_count(self) -> bool:
        """``{`` starts a count only if it parses as one; otherwise it is a literal."""
        probe = self.index + 1
        digits = 0
        while probe < len(self.source) and self.source[probe].isdigit():
            probe += 1
            digits += 1
        if digits == 0:
            return False
        if probe < len(self.source) and self.source[probe] == ",":
            probe += 1
            while probe < len(self.source) and self.source[probe].isdigit():
                probe += 1
        return probe < len(self.source) and self.source[probe] == "}"

    def counted(self) -> tuple[int, int | None]:
        closing = self.source.index("}", self.index)
        body = self.source[self.index + 1 : closing]
        self.index = closing + 1
        low_text, comma, high_text = body.partition(",")
        low = int(low_text)
        if not comma:
            high: int | None = low
        elif high_text == "":
            high = None
        else:
            high = int(high_text)
        if low > MAX_REPEAT or (high is not None and high > MAX_REPEAT):
            raise PatternSyntaxError(
                f"repetition count above {MAX_REPEAT} in {self.source!r}. A bound that "
                "large is a way to build an automaton rather than a rule about a value"
            )
        if high is not None and high < low:
            raise self.fail(f"repetition {{{body}}} counts down")
        return low, high

    def quantify(self, atom: _Fragment, char: str) -> _Fragment:
        """``*``, ``+``, and ``?`` -- one split each, no backtracking anywhere."""
        split = self.node("split")
        if char == "*":
            split.out = atom.start
            atom.patch(split)
            return _Fragment(split, [(split, 2)])
        if char == "+":
            split.out = atom.start
            atom.patch(split)
            return _Fragment(atom.start, [(split, 2)])
        split.out = atom.start
        return _Fragment(split, [*atom.exits, (split, 2)])

    def repeat(self, source: str, low: int, high: int | None, atom: _Fragment) -> _Fragment:
        """Counted repetition, by re-parsing the atom's source once per copy.

        Re-parsing rather than deep-copying is what keeps the node budget honest:
        every copy is counted as it is built, so ``(\\d{100}){100}`` trips
        ``MAX_NODES`` while being constructed instead of after twenty thousand
        states already exist. The atom already built is reused as the first copy,
        so the common ``{1}`` costs nothing extra.
        """
        pieces: list[_Fragment] = []
        if low >= 1:
            pieces.append(atom)
            pieces.extend(self.subparse(source) for _ in range(low - 1))
            if high is None:
                pieces.append(self.quantify(self.subparse(source), "*"))
            else:
                pieces.extend(self.quantify(self.subparse(source), "?") for _ in range(high - low))
        elif high is None:
            pieces.append(self.quantify(atom, "*"))
        elif high >= 1:
            pieces.append(self.quantify(atom, "?"))
            pieces.extend(self.quantify(self.subparse(source), "?") for _ in range(high - 1))
        else:
            # `{0}` matches the empty string. The atom was parsed and is now
            # unreachable; its states stay counted, which is the conservative
            # direction for a budget.
            return self.empty()
        result = pieces[0]
        for piece in pieces[1:]:
            result.patch(piece.start)
            result = _Fragment(result.start, piece.exits)
        return result

    def subparse(self, body: str) -> _Fragment:
        """One more copy of an atom's source, parsed into fresh states."""
        inner = _Parser(body)
        inner.count = self.count
        fragment = inner.atom()
        if inner.index != len(body):
            raise self.fail(f"cannot repeat {body!r}")
        self.count = inner.count
        if self.count > MAX_NODES:
            raise PatternSyntaxError(
                f"pattern expands past {MAX_NODES} automaton states in {self.source!r}"
            )
        return fragment

    def atom(self) -> _Fragment:
        char = self.peek()
        if char == "(":
            self.reject_unsupported()
            self.index += 1
            if self.source[self.index : self.index + 2] == "?:":
                self.index += 2
            fragment = self.alternation()
            if self.peek() != ")":
                raise self.fail("unbalanced '('")
            self.index += 1
            return fragment
        if char == "[":
            return self.character_class()
        if char == "\\":
            return self.escape()
        if char == ".":
            self.index += 1
            node = self.node("char", lambda ch: ch != "\n")
            return _Fragment(node, [(node, 1)])
        if char in ("*", "+", "?"):
            raise self.fail(f"nothing to repeat before {char!r}")
        if char == ")":
            raise self.fail("unbalanced ')'")
        if char in ("^", "$"):
            raise PatternSyntaxError(
                f"anchor {char!r} at position {self.index} of {self.source!r} is not at the "
                "edge of the pattern. A pattern constraint matches the whole value, so an "
                "anchor in the middle could never match"
            )
        self.index += 1
        node = self.node("char", _literal(char))
        return _Fragment(node, [(node, 1)])

    def reject_unsupported(self) -> None:
        for prefix, description in _UNSUPPORTED.items():
            if self.source.startswith(prefix, self.index):
                raise PatternSyntaxError(
                    f"{description} ({prefix}...) is not part of {PATTERN_DIALECT_VERSION}. "
                    "The dialect is deliberately small so that matching stays linear in "
                    "the length of the value"
                )

    def escape(self) -> _Fragment:
        self.index += 1
        if self.index >= len(self.source):
            raise self.fail("pattern ends with a trailing backslash")
        char = self.source[self.index]
        self.index += 1
        if char.isdigit() and char != "0":
            raise PatternSyntaxError(
                f"backreference \\{char} is not part of {PATTERN_DIALECT_VERSION}. "
                "Backreferences cannot be matched by a finite automaton, which is what "
                "makes this dialect's time bound possible"
            )
        if char in ("b", "B", "A", "Z"):
            raise PatternSyntaxError(
                f"the zero-width assertion \\{char} is not part of {PATTERN_DIALECT_VERSION}"
            )
        predicate = self._predicate_for_escape(char)
        node = self.node("char", predicate)
        return _Fragment(node, [(node, 1)])

    @staticmethod
    def _predicate_for_escape(char: str) -> Predicate:
        klass = _CLASS_ESCAPES.get(char)
        if klass is not None:
            return klass
        return _literal(_LITERAL_ESCAPES.get(char, char))

    def character_class(self) -> _Fragment:
        self.index += 1
        negated = self.peek() == "^"
        if negated:
            self.index += 1
        members: list[Predicate] = []
        while self.peek() and self.peek() != "]":
            members.append(self.class_member())
        if self.peek() != "]":
            raise self.fail("unbalanced '['")
        self.index += 1
        if not members:
            raise self.fail("empty character class")
        frozen = tuple(members)

        def predicate(ch: str) -> bool:
            hit = any(member(ch) for member in frozen)
            return not hit if negated else hit

        node = self.node("char", predicate)
        return _Fragment(node, [(node, 1)])

    def class_member(self) -> Predicate:
        if self.peek() == "\\":
            self.index += 1
            char = self.source[self.index]
            self.index += 1
            if char.isdigit() and char != "0":
                raise PatternSyntaxError(
                    f"backreference \\{char} is not part of {PATTERN_DIALECT_VERSION}"
                )
            return self._predicate_for_escape(char)
        low = self.peek()
        self.index += 1
        if self.peek() == "-" and self.source[self.index + 1 : self.index + 2] not in ("]", ""):
            self.index += 1
            high = self.peek()
            self.index += 1
            if high < low:
                raise self.fail(f"character range {low!r}-{high!r} runs backwards")
            return _range(low, high)
        return _literal(low)


def compile_pattern(source: str) -> Pattern:
    """Compile one ``pattern`` constraint, or raise ``PatternSyntaxError``.

    A leading ``^`` and a trailing ``$`` are stripped as redundant: matching is
    whole-value already, and an author who writes them means the same thing. An
    anchor anywhere else is rejected rather than ignored, because ignoring it
    would silently change what the author asked for.
    """
    if not isinstance(source, str):
        raise PatternSyntaxError(f"a pattern must be a string, got {type(source).__name__}")
    body = source
    if body.startswith("^"):
        body = body[1:]
    if body.endswith("$") and not body.endswith("\\$"):
        body = body[:-1]
    parser = _Parser(body)
    start = parser.parse()
    return Pattern(source, start, parser.count)
