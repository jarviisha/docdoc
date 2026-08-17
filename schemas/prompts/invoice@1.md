You are reading one invoice. Return the fields the response schema declares, and nothing else.

For every field, return two things:

- `value` — the field's value, typed as the schema declares it. Use `null` when the document does
  not contain the field. `null` is a correct answer; a guess is not.
- `claimed_text` — the text **exactly as it appears in the document**, character for character,
  that you read the value from. Do not tidy it, reformat it, or normalise it: if the document says
  `1,240.00`, that is the claimed text even when the value is `1240.00`. If you did not read the
  value from any specific text, return `null` here too.

The claimed text is how a later stage locates the value on the page. Text you have altered cannot
be located, so an altered claim is worse than an absent one.

Rules:

- Do not infer a field from another. A due date is not "issue date plus thirty days" unless the
  document says so.
- Take amounts as printed. Do not recalculate a total from the line items, and do not correct the
  document when they disagree — report what it says.
- Include every charged line in `line_items`, in the order printed. Do not merge or summarise them.
