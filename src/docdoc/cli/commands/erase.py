"""``docdoc erase`` — remove a customer's data, or one document's, on request.

**This is where `--purge-store-root` lives, and it exists at no URL.**

The default tenant's namespace is the store root (ADR-0014 §3), so erasing it by
prefix removes everything written before authentication was enabled and
everything ``docdoc extract`` ever wrote. ADR-0014 predicted this milestone would
build the naive version and said why the correct semantics cannot be the default
behaviour: an operator erasing "the default tenant" on a deployment that never
enabled authentication is not erasing a customer.

So there are three behaviours and the operator picks by typing:

* ``--tenant acme`` — a named tenant, by prefix. One operation, not a scan.
* ``--tenant default`` — the run-derived content of the default tenant, by set
  difference. Content no run produced is untouched.
* ``--tenant default --purge-store-root`` — the whole store. The other reading,
  available, and typed deliberately once.

The HTTP route refuses the default tenant outright and points here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docdoc.artifacts.paths import root_tenant
from docdoc.cli.render import Rendering
from docdoc.runs import retention
from docdoc.runs.identity import now as clock

if TYPE_CHECKING:
    import argparse

    from docdoc.cli.config import Settings

__all__ = ["run"]


def run(args: argparse.Namespace, settings: Settings) -> Rendering:
    """Erase, and report counts by kind."""

    tenant_id: str = args.tenant
    blob_id: str | None = getattr(args, "document", None)
    purge_root: bool = getattr(args, "purge_store_root", False)
    is_default = tenant_id == root_tenant()

    artifacts, blobs = settings.stores_for(tenant_id)
    report = retention.erase(
        settings.run_queue(getattr(args, "run_database_url", None)),
        retention.Stores(artifacts=artifacts, blobs=blobs),
        tenant_id=tenant_id,
        now=clock(),
        blob_id=blob_id,
        is_default_tenant=is_default,
        allow_store_root=purge_root,
    )

    lines = [
        f"removed {report.runs} run(s), {report.artifacts} artifact(s), "
        f"{report.blobs} blob(s), {report.rows} other row(s)"
    ]
    if is_default and not purge_root and blob_id is None:
        # Said every time rather than only when something survives, because the
        # operator's mental model is what this is correcting and the surprising
        # case is the one where nothing was left to keep.
        lines.append(
            "the default tenant's namespace is the store root, so only its "
            "run-derived content was removed. Content written outside a run -- by "
            "`docdoc extract`, by a library caller, by the recorder -- is "
            "untouched. --purge-store-root removes the whole store"
        )
    if report.degraded:
        lines = ["the store could not be reached; nothing was removed"]

    return Rendering(
        code=0,
        data={
            "runs": report.runs,
            "artifacts": report.artifacts,
            "blobs": report.blobs,
            "rows": report.rows,
            "degraded": report.degraded,
        },
        lines=lines,
    )
