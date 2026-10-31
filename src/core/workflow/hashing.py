"""Deterministic canonical SHA-256 hashing for workflow definitions."""

import hashlib
import json
from typing import TYPE_CHECKING, Any

from src.core.contracts.artifact import Sha256Hash
from src.core.workflow.freezer import unfreeze_value

if TYPE_CHECKING:
    from src.core.workflow.definition import WorkflowDefinition


def compute_canonical_workflow_hash(defn: "WorkflowDefinition") -> Sha256Hash:
    """
    Compute a deterministic canonical SHA-256 hash across all semantic fields.

    Inclusion Policy:
    Includes all fields that define workflow structure, behavior, and identity:
    - workflow_id, version, name, description, state_schema
    - metadata (keys sorted)
    - inputs (sorted by key)
    - steps (sorted by step_id, with depends_on/inputs/outputs sorted lexicographically)

    Deterministic JSON Rules:
    - UTF-8 encoding
    - Compact separators (',', ':')
    - Keys sorted
    - allow_nan=False (strictly rejects NaN/Infinity)
    - ensure_ascii=False
    """
    canonical_payload: dict[str, Any] = {
        "description": defn.description,
        "inputs": [
            {
                "description": inp.description,
                "key": str(inp.key),
                "required": inp.required,
            }
            for inp in sorted(defn.inputs, key=lambda x: str(x.key))
        ],
        "metadata": unfreeze_value(defn.metadata),
        "name": str(defn.name),
        "state_schema": str(defn.state_schema),
        "steps": [
            {
                "capability": str(s.capability),
                "config": unfreeze_value(s.config),
                "depends_on": sorted(str(d) for d in s.depends_on),
                "description": s.description,
                "inputs": sorted(str(i) for i in s.inputs),
                "name": str(s.name),
                "outputs": sorted(str(o) for o in s.outputs),
                "step_id": str(s.step_id),
            }
            for s in sorted(defn.steps, key=lambda x: str(x.step_id))
        ],
        "version": defn.version,
        "workflow_id": str(defn.workflow_id),
    }

    canonical_json = json.dumps(
        canonical_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )

    digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return Sha256Hash(digest)

