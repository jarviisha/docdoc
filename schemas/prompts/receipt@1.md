You are reading one retail receipt. Return the fields the response schema declares, and nothing else.

For every field, return two things:

- `value` — the field's value, typed as the schema declares it. Use `null` when the receipt does not
  contain the field.
- `claimed_text` — the text **exactly as printed**, character for character, that you read the value
  from. Do not tidy or reformat it.

**Number formatting.** A `decimal` or `number` value must be machine-parseable: a `.` decimal
separator, no thousands separators, no currency symbol. The document's own formatting belongs in
`claimed_text` and only there.

    printed 2.480,50  ->  value "2480.50",  claimed_text "2.480,50"
    printed 1,240.00  ->  value "1240.00",  claimed_text "1,240.00"

This applies **inside repeating groups too**, not only to top-level fields. Do not normalise the
claimed text, and do not leave the value in the document's format -- they are two different fields
answering two different questions.

Rules:

- `total` is the amount actually paid, not a subtotal and not a pre-discount figure.
- If the receipt prints a date but no time, use midnight on that date.
- Classify `payment_method` only from what the receipt states. Do not infer "card" from the presence
  of a terminal reference number alone.
