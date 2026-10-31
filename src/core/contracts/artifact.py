"""Artifact metadata references and cryptographic hash validation."""

import re
from typing import Annotated
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from src.core.contracts.ids import ArtifactId

SHA256_HEX_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def validate_and_normalize_sha256(v: str) -> str:
    """Normalize hex digest to lowercase and validate 64-character SHA-256 format."""
    normalized = v.strip().lower()
    if not SHA256_HEX_PATTERN.match(normalized):
        raise ValueError("Must be a valid 64-character lowercase SHA-256 hex string")
    return normalized


# Reusable validated SHA-256 type shared across ArtifactRef and ApprovalDecision
Sha256Hash = Annotated[
    str,
    StringConstraints(strip_whitespace=True),
    AfterValidator(validate_and_normalize_sha256),
]


class ArtifactRef(BaseModel):
    """Immutable reference describing an artifact's identity, version, and content digest."""

    model_config = ConfigDict(frozen=True)

    artifact_id: ArtifactId
    version: int = Field(ge=1, description="Monotonically increasing version number")
    content_hash: Sha256Hash
    media_type: str | None = Field(default=None, description="MIME type e.g. text/markdown, application/json")
    uri: str | None = Field(default=None, description="Optional storage URI e.g. file://, s3://")

