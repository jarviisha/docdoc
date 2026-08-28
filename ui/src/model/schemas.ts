/**
 * The schemas a deployment offers, and what to say when it offers none.
 *
 * An empty list is a valid deployment state, not an error (FR-012). Milestone 7
 * fixed the same failure for the command line: an error that says only "the
 * schema does not exist" sends the caller to read the registry by hand, when
 * what they need is the name of the setting that populates it (FR-026).
 */

export interface SchemaChoice {
  identity: string;
}

interface WireListing {
  schemas?: { identity: string }[];
}

export function toSchemaChoices(listing: WireListing): SchemaChoice[] {
  return (listing.schemas ?? []).map((entry) => ({ identity: entry.identity }));
}

/**
 * The message for a deployment that has no schemas — and **only** for one.
 *
 * `null` means say nothing: either there is something to choose from, or the
 * listing has not arrived yet and `choices` is `null`.
 *
 * That second case is why this takes a nullable. Until T080 the caller held
 * `SchemaChoice[]` initialised to `[]`, so on the very first paint — before the
 * request had returned — the interface stated that the deployment had no schemas
 * configured. It was a claim about the deployment, made before anything had been
 * asked, and it was false on every deployment that has any. A screenshot of the
 * landing page is what found it: no model test could, because the fixture always
 * has a listing, and no component test exists at all.
 *
 * "Not yet known" and "known to be none" are the same distinction this milestone
 * spends FR-018 keeping apart for geometry. Collapsing it here was the same
 * mistake in a different place.
 */
export function emptyRegistryNotice(choices: SchemaChoice[] | null): string | null {
  if (choices === null) return null;
  if (choices.length > 0) return null;
  return (
    "This deployment has no schemas configured, which is a valid configuration " +
    "and not a fault. Set DOCDOC_SCHEMA_PATHS to a directory of schemas to " +
    "populate the list."
  );
}
