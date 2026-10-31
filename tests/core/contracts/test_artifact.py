"""Unit tests for ArtifactRef and SHA-256 hash validation/normalization."""

import pytest
from pydantic import ValidationError

from src.core.contracts.artifact import ArtifactRef

VALID_LOWER_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
VALID_UPPER_HASH = "E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855"


def test_artifact_ref_valid_creation():
    ref = ArtifactRef(
        artifact_id="art-1",
        version=1,
        content_hash=VALID_LOWER_HASH,
        media_type="text/markdown",
        uri="file:///workspace/README.md",
    )
    assert ref.artifact_id == "art-1"
    assert ref.version == 1
    assert ref.content_hash == VALID_LOWER_HASH
    assert ref.media_type == "text/markdown"
    assert ref.uri == "file:///workspace/README.md"


def test_sha256_uppercase_normalization():
    ref = ArtifactRef(
        artifact_id="art-1",
        version=1,
        content_hash=f"  {VALID_UPPER_HASH}  ",
    )
    # Must be stripped and lowercased
    assert ref.content_hash == VALID_LOWER_HASH


@pytest.mark.parametrize(
    "invalid_hash",
    [
        "",                                      # Empty
        "   ",                                   # Whitespace
        VALID_LOWER_HASH[:63],                   # Too short (63 chars)
        VALID_LOWER_HASH + "a",                  # Too long (65 chars)
        "z" * 64,                                # Non-hex characters
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85g", # 'g' is invalid hex
    ],
)
def test_sha256_invalid_rejected(invalid_hash: str):
    with pytest.raises(ValidationError):
        ArtifactRef(
            artifact_id="art-1",
            version=1,
            content_hash=invalid_hash,
        )


def test_artifact_version_must_be_ge_1():
    with pytest.raises(ValidationError):
        ArtifactRef(
            artifact_id="art-1",
            version=0,
            content_hash=VALID_LOWER_HASH,
        )

    with pytest.raises(ValidationError):
        ArtifactRef(
            artifact_id="art-1",
            version=-5,
            content_hash=VALID_LOWER_HASH,
        )


def test_artifact_ref_immutability():
    ref = ArtifactRef(
        artifact_id="art-1",
        version=1,
        content_hash=VALID_LOWER_HASH,
    )
    with pytest.raises(ValidationError):
        ref.version = 2  # type: ignore[misc]

