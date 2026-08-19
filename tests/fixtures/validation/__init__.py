"""Fixtures for Milestone 5.

Three modules, deliberately separate: `schemas` declares what a document must
look like, `rules` declares the cross-field obligations, and `artifacts` builds
the extraction/grounding pairs the stage actually validates.

Everything here is offline. The documents are hand-built, the extraction results
are constructed rather than requested from a provider, and the grounding results
come from the real grounder — so a fixture cannot claim a location the grounding
stage would not have produced.
"""
