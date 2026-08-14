# Identity: blob vs. document

docdoc has **two** identities, and conflating them is a correctness bug rather than a naming
preference. See [ADR-0002](../adr/0002-blob-and-document-identity.md) for the decision record.

```text
blob_id     = sha256(original_bytes)
document_id = sha256(canonical_json({
                  "v": 1, "blob_id": ..., "parser_id": ...,
                  "parser_version": ..., "options_hash": ...,
              }))
```

Both are formatted as `sha256:<64 lowercase hex>`.

| | Identifies | Changes when |
|---|---|---|
| `blob_id` | the **source file** | the bytes change |
| `document_id` | **one parse** of that file | the bytes, parser, version, or options change |

## Why two

Spans and geometry are only meaningful relative to a *specific parse*. The same PDF read by a
native text parser and by a cloud document-intelligence provider produces different canonical
text, different offsets, and different token geometry.

Under a bytes-only identity, both parses would share one id while carrying mutually incompatible
spans. Nothing would stop a caller applying one parse's span to the other, and the result would be
a confidently wrong bounding box rather than an error. That is the worst kind of failure for a
system whose purpose is traceability.

So: **all spans and geometry anchor to `document_id`.** Two parses of one file share a `blob_id`
and get different `document_id`s.

```python
blob = blob_id_for(pdf_bytes)

fast = document_id_for(
    blob_id=blob, parser_id="pdf_text", parser_version="1.0.0", options_hash=opts
)
good = document_id_for(
    blob_id=blob, parser_id="cloud_di", parser_version="2024-11-30", options_hash=opts
)

assert fast != good  # incompatible spans, incompatible identities
```

In practice `blob_id` is the user-facing handle — it deduplicates uploads and answers "have I seen
this file?" — while `document_id` is the processing handle that results and spans reference.

## Named fields, not concatenation

Identity inputs are hashed as a **canonical JSON object with named fields**. The original design
sketched concatenation, which is ambiguous:

```text
parser_id="pdf"  + version="1.0"   ->  "pdf1.0"
parser_id="pdf1" + version=".0"    ->  "pdf1.0"     # same input, same identity
```

Two genuinely different parse configurations would collide. A structured encoding removes the
class entirely, and `tests/unit/test_identity.py` asserts exactly this case.

## Canonical JSON

```python
json.dumps(
    value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
).encode("utf-8")
```

- **Sorted keys** — `{"a": 1, "b": 2}` and `{"b": 2, "a": 1}` must hash identically. Options
  arriving in a different order is not a different configuration.
- **Compact separators** — whitespace must not affect identity.
- **`ensure_ascii=False`** — non-ASCII is preserved rather than escaped, so `"Công ty"` encodes as
  itself.
- **`allow_nan=False`** — `NaN` and `±Infinity` break both JSON interop and hash stability, and are
  rejected with `IdentityError` rather than coerced.

Non-string dict keys and non-JSON values are rejected too, with the offending path named in the
error.

## A caveat on float options

CPython's `float.__repr__` produces the shortest round-tripping representation and is
platform-independent for IEEE-754 doubles, so float options hash consistently across machines.

Even so, prefer strings, ints, and bools in `ParseOptions`. A float computed differently on two
platforms — say a DPI derived from a division — could differ in its last bit and silently
fragment your cache. This is documentation rather than enforcement, because a legitimate float
option (a threshold, a scale factor) is plausible.

## Versioned derivation

`IDENTITY_SCHEMA_VERSION` is embedded in the hashed payload as `"v"`. If the derivation itself ever
changes, old identities stay interpretable and new ones are visibly different — rather than the
two silently overlapping.

## Identity is not affected by slicing

Slicing does not change the blob, the parser, the version, or the options, so a slice keeps the
same `document_id`. What distinguishes it is `Document.origin`: the ranges of the original parse
it occupies.

```python
document.slice(Span(12, 19)).origin  # (Span(12, 19),)
```

`origin` is what makes `merge` able to reject overlapping or out-of-order parts. Without it, merge
would have no way to know whether two parts describe the same region of the source.
