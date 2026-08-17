You are reading one retail receipt. Return the fields the response schema declares, and nothing else.

For every field, return two things:

- `value` — the field's value, typed as the schema declares it. Use `null` when the receipt does not
  contain the field.
- `claimed_text` — the text **exactly as printed**, character for character, that you read the value
  from. Do not tidy or reformat it.

Rules:

- `total` is the amount actually paid, not a subtotal and not a pre-discount figure.
- If the receipt prints a date but no time, use midnight on that date.
- Classify `payment_method` only from what the receipt states. Do not infer "card" from the presence
  of a terminal reference number alone.
