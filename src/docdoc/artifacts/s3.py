"""S3-backed stores, so two workers can reuse each other's artifacts.

Without a shared store, scaling from one worker to three produces **correct
results and silently re-pays for every parse**: each worker has a private root,
every lookup misses, and nothing anywhere reports a problem. That is the failure
SC-005 exists to catch and the reason this file is in Milestone 9 rather than the
next one.

**These inherit ADR-0010's rules rather than restating them.** §4's four read
outcomes and §5's no-overwrite rule were written for one process and happen to be
exactly right for several — §5 even argues the concurrent case: "atomic
replacement of an immutable, content-addressed entry is what makes the race
benign". Every behaviour below is that decision applied to a different medium.

**`boto3` is imported lazily**, inside the constructor. Principle IV isolates
*provider* SDKs — the parsers and models that produce document content — and an
object store client is not that: the sanctioned stack names "local filesystem or
S3-compatible object storage" as infrastructure, in the same sentence as
PostgreSQL. Reading `boto3` as a provider SDK would make the constitution forbid
the storage its own stack line permits (research R5). The laziness is what keeps
a base install free of it regardless.

**Atomicity comes from the medium.** A single-part `PutObject` is atomic in
S3-compatible stores: a reader sees the old object or the new one, never a
partial. That is what the filesystem store obtains with temp-file-and-link, and
it is why nothing here needs a protocol of its own.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, TypeVar

from docdoc.artifacts.envelope import ArtifactEnvelope, content_id_of
from docdoc.artifacts.errors import ArtifactError
from docdoc.artifacts.paths import tenant_root
from docdoc.kernel.identity import blob_id_for

if TYPE_CHECKING:
    from pydantic import BaseModel

from pydantic import BaseModel as _BaseModel
from pydantic import ValidationError

M = TypeVar("M", bound=_BaseModel)

__all__ = ["S3ArtifactStore", "S3BlobStore", "s3_client"]

_logger = logging.getLogger("docdoc.artifacts")


def s3_client(
    *,
    endpoint_url: str | None = None,
    region_name: str | None = None,
    **kwargs: Any,
) -> Any:
    """A boto3 S3 client, imported at call time.

    A function rather than a module-level import so that `docdoc.artifacts` keeps
    its base install free of `boto3` — the layer sits directly above the kernel
    and every other thing it depends on is `pydantic` and two kernel helpers.
    """
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - guarded by the extra
        raise ArtifactError(
            "boto3 is not installed; object storage needs `pip install docdoc[s3]`",
            reason="unavailable",
        ) from exc

    return boto3.client(
        "s3", endpoint_url=endpoint_url, region_name=region_name or "us-east-1", **kwargs
    )


def _digest_of(artifact_id: str) -> str:
    digest = artifact_id.split(":", 1)[-1]
    if not digest or not all(character in "0123456789abcdef" for character in digest):
        raise ArtifactError(
            f"{artifact_id!r} is not a content-addressed identity",
            reason="malformed_id",
            artifact_id=artifact_id,
        )
    return digest


def _is_missing(error: Exception) -> bool:
    """Whether a boto3 error means "not there" rather than "cannot reach".

    The distinction is ADR-0010 §4's, and getting it wrong in either direction is
    a real fault: treating an outage as a miss re-runs a billable stage, and
    treating a miss as an outage stops reuse working at all.
    """
    response = getattr(error, "response", None)
    if not isinstance(response, dict):
        return False
    code = str(response.get("Error", {}).get("Code", ""))
    return code in {"NoSuchKey", "404", "NoSuchBucket"}


class _S3Base:
    """Bucket, prefix, and the one tenant rule both stores share."""

    def __init__(
        self,
        bucket: str,
        *,
        client: Any = None,
        prefix: str = "",
        tenant_id: str = "default",
        endpoint_url: str | None = None,
    ) -> None:
        self._bucket = bucket
        self._client = client if client is not None else s3_client(endpoint_url=endpoint_url)
        # `tenant_root` returns "" for the default tenant, so an existing
        # deployment's objects stay exactly where they are (FR-084a). Do not
        # "tidy" this into an unconditional prefix; see that function's docstring.
        parts = [part for part in (prefix.strip("/"), tenant_root(tenant_id)) if part]
        self._prefix = "/".join(parts)

    def _key(self, *parts: str) -> str:
        return "/".join([*([self._prefix] if self._prefix else []), *parts])

    @property
    def root(self) -> str:
        """What the error model reports as the store's location."""
        return f"s3://{self._bucket}/{self._prefix}" if self._prefix else f"s3://{self._bucket}"


class S3BlobStore(_S3Base):
    """Source bytes in an object store, keyed by their own content."""

    def _key_for(self, blob_id: str) -> str:
        digest = _digest_of(blob_id)
        return self._key("blobs", digest[:2], digest)

    def put(self, data: bytes) -> str:
        """Store bytes and return their identity. Idempotent.

        No existence check first. Content-addressed means an object under this
        key *is* these bytes, so a second write is the same write — and skipping
        the check saves a round trip on the path that runs for every submission.
        """
        blob_id = blob_id_for(data)
        self._client.put_object(Bucket=self._bucket, Key=self._key_for(blob_id), Body=data)
        return blob_id

    def get(self, blob_id: str) -> bytes | None:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._key_for(blob_id))
        except Exception as error:
            if _is_missing(error):
                return None
            _logger.warning(
                "blob store unreachable",
                extra={
                    "event": "artifacts.blob_unreadable",
                    "blob_id": blob_id,
                    "error": type(error).__name__,
                },
            )
            return None
        return bytes(response["Body"].read())

    def size_of(self, blob_id: str) -> int | None:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=self._key_for(blob_id))
        except Exception as error:
            if not _is_missing(error):
                _logger.warning(
                    "blob store unreachable",
                    extra={"event": "artifacts.blob_unreadable", "blob_id": blob_id},
                )
            return None
        return int(response["ContentLength"])


class S3ArtifactStore(_S3Base):
    """Satisfies `ArtifactStore` over an object store."""

    def _key_for(self, artifact_id: str) -> str:
        digest = _digest_of(artifact_id)
        return self._key("artifacts", digest[:2], f"{digest}.json")

    # -- reading -----------------------------------------------------------

    def get(
        self,
        artifact_id: str,
        *,
        model: type[M],
        artifact_format_version: int,
    ) -> M | None:
        stored = self.envelope(artifact_id)
        if stored is None:
            return None

        if stored.artifact_format_version != artifact_format_version:
            # A miss, and logged so the cost is explicable. ADR-0010 §4: a format
            # bump is an expected event on upgrade, and making it fatal would
            # break every run until somebody emptied a bucket by hand.
            _logger.info(
                "artifact format mismatch, recomputing",
                extra={
                    "event": "artifacts.format_mismatch",
                    "artifact_id": artifact_id,
                    "stored_version": stored.artifact_format_version,
                    "expected_version": artifact_format_version,
                },
            )
            return None

        try:
            return model.model_validate(stored.payload)
        except ValidationError as error:
            raise ArtifactError(
                f"stored artifact does not fit {model.__name__} although its format "
                f"version claims to; the version was probably not bumped",
                reason="model_mismatch",
                artifact_id=artifact_id,
                root=self.root,
                stage=stored.stage,
            ) from error

    def envelope(self, artifact_id: str) -> ArtifactEnvelope | None:
        """Read and verify one envelope.

        Verification happens here rather than in `get`, so that reading an
        envelope for any reason — including explaining a derivation — cannot
        return one whose bytes are not what was written.
        """
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._key_for(artifact_id))
            raw = response["Body"].read().decode("utf-8")
        except Exception as error:
            if _is_missing(error):
                return None
            # Unreachable is not the same as absent, but for a *cache* it has the
            # same correct response: proceed without it (ADR-0010 §4, FR-063).
            _logger.warning(
                "artifact store unreachable, continuing without reuse",
                extra={
                    "event": "artifacts.unreadable",
                    "artifact_id": artifact_id,
                    "error": type(error).__name__,
                },
            )
            return None

        try:
            stored = ArtifactEnvelope.model_validate_json(raw)
        except ValidationError as error:
            raise ArtifactError(
                "stored artifact is not a readable envelope",
                reason="integrity",
                artifact_id=artifact_id,
                root=self.root,
            ) from error

        if not stored.content_matches():
            raise ArtifactError(
                "stored artifact does not match the content id recorded beside it; "
                "the bytes in the object store are not what was written",
                reason="integrity",
                artifact_id=artifact_id,
                root=self.root,
                stage=stored.stage,
            )

        if stored.artifact_id != artifact_id:
            raise ArtifactError(
                "stored artifact carries a different identity than the one it is filed under",
                reason="integrity",
                artifact_id=artifact_id,
                root=self.root,
                stage=stored.stage,
            )
        return stored

    # -- writing -----------------------------------------------------------

    def put(
        self,
        artifact_id: str,
        payload: BaseModel,
        *,
        stage: str,
        input_artifact_id: str | None,
        processor_id: str,
        processor_version: str,
        options_hash: str,
        artifact_format_version: int,
    ) -> None:
        """Store a result. A no-op if an identical one is already there.

        ADR-0010 §5, unchanged: identical content is a no-op and divergent
        content raises. The read-then-write here is not a lock and does not need
        to be — two workers writing *identical* bytes is the benign race §5
        describes, and two workers writing *different* bytes under one
        content-addressed identity is the fault the rule exists to surface, which
        the second check below catches whichever of them lands last.
        """
        serialised: dict[str, Any] = json.loads(payload.model_dump_json())

        existing = self.envelope(artifact_id)
        if existing is not None:
            if existing.content_id == content_id_of(serialised):
                return
            raise ArtifactError(
                "a different result is already stored under this identity; an "
                "identity covers every result-affecting input, so this means "
                "either corruption or a processor whose output moved without its "
                "version moving (ADR-0003)",
                reason="conflict",
                artifact_id=artifact_id,
                root=self.root,
                stage=stage,
            )

        envelope = ArtifactEnvelope.build(
            artifact_id=artifact_id,
            stage=stage,
            input_artifact_id=input_artifact_id,
            processor_id=processor_id,
            processor_version=processor_version,
            options_hash=options_hash,
            artifact_format_version=artifact_format_version,
            payload=serialised,
        )

        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=self._key_for(artifact_id),
                Body=envelope.model_dump_json(indent=2).encode("utf-8"),
            )
        except Exception as error:
            # A store that cannot be written to degrades rather than failing the
            # run (FR-063): the result is already computed and correct, and
            # losing a cache entry is not a reason to lose it.
            _logger.warning(
                "artifact store unwritable, continuing without caching",
                extra={
                    "event": "artifacts.unwritable",
                    "artifact_id": artifact_id,
                    "error": type(error).__name__,
                },
            )
            return

        # Re-read, because unlike the filesystem store there is no exclusive
        # create: `PutObject` overwrites. A racing writer with different content
        # would otherwise be accepted in silence, which is the exact failure the
        # conflict rule exists to prevent.
        landed = self.envelope(artifact_id)
        if landed is None or landed.content_id == envelope.content_id:
            return
        raise ArtifactError(
            "a different result was stored under this identity concurrently; an "
            "identity covers every result-affecting input, so this means either "
            "corruption or a processor whose output moved without its version "
            "moving (ADR-0003)",
            reason="conflict",
            artifact_id=artifact_id,
            root=self.root,
            stage=stage,
        )

    def clear(self, *, stage: str | None = None) -> int:
        """Remove everything, or one stage. Returns how many were removed.

        Clearing one stage needs each envelope read, because the stage is inside
        the object and not in its key. That is a deliberate consequence of ADR-0010
        §1's layout: the key is the identity and nothing else, so nothing about a
        stage can be inferred from a listing.
        """
        removed = 0
        paginator = self._client.get_paginator("list_objects_v2")
        prefix = self._key("artifacts")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for entry in page.get("Contents", ()):
                key = entry["Key"]
                if stage is not None:
                    try:
                        raw = self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
                        if ArtifactEnvelope.model_validate_json(raw).stage != stage:
                            continue
                    except Exception:
                        continue
                self._client.delete_object(Bucket=self._bucket, Key=key)
                removed += 1
        return removed
