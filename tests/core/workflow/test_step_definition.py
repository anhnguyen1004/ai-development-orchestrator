"""Unit tests for StepDefinition model, Capability constraints, and config freezing."""

import pytest
from pydantic import ValidationError

from src.core.workflow.step_definition import StepDefinition


@pytest.mark.parametrize(
    "valid_cap",
    [
        "requirement.analysis",
        "architecture.design",
        "code.implementation",
        "quality.assurance",
        "code.review",
        "human.approval",
        "pipeline.deploy.prod",
    ],
)
def test_valid_capabilities(valid_cap: str):
    step = StepDefinition(
        step_id="step_1",
        name="Step 1",
        capability=valid_cap,
    )
    assert step.capability == valid_cap


@pytest.mark.parametrize(
    "invalid_cap",
    [
        "analysis",              # No dot
        "Requirement.Analysis",  # Uppercase
        "code implementation",   # Space
        ".leading.dot",          # Leading dot
        "trailing.dot.",         # Trailing dot
        "code..double_dot",      # Consecutive dots
        "special$char.test",     # Invalid character
        " requirement.analysis", # Leading whitespace
        "requirement.analysis ", # Trailing whitespace
        "requirement. analysis", # Whitespace after dot
    ],
)
def test_invalid_capabilities_rejected(invalid_cap: str):
    with pytest.raises(ValidationError):
        StepDefinition(
            step_id="step_1",
            name="Step 1",
            capability=invalid_cap,
        )


def test_strict_capability_whitespace_rules():
    # "requirement.analysis" -> accepted
    step = StepDefinition(
        step_id="step_1",
        name="Step 1",
        capability="requirement.analysis",
    )
    assert step.capability == "requirement.analysis"

    # Leading whitespace -> rejected
    with pytest.raises(ValidationError):
        StepDefinition(
            step_id="step_1",
            name="Step 1",
            capability=" requirement.analysis",
        )

    # Trailing whitespace -> rejected
    with pytest.raises(ValidationError):
        StepDefinition(
            step_id="step_1",
            name="Step 1",
            capability="requirement.analysis ",
        )

    # Whitespace after dot -> rejected
    with pytest.raises(ValidationError):
        StepDefinition(
            step_id="step_1",
            name="Step 1",
            capability="requirement. analysis",
        )


def test_reject_self_dependency():
    with pytest.raises(ValidationError, match="cannot depend on itself"):
        StepDefinition(
            step_id="step_a",
            name="Step A",
            capability="code.implementation",
            depends_on=("step_a",),
        )


def test_reject_duplicate_depends_on():
    with pytest.raises(ValidationError, match="duplicate depends_on entries"):
        StepDefinition(
            step_id="step_b",
            name="Step B",
            capability="code.implementation",
            depends_on=("step_a", "step_a"),
        )


def test_reject_duplicate_inputs_and_outputs():
    with pytest.raises(ValidationError, match="duplicate inputs declarations"):
        StepDefinition(
            step_id="step_c",
            name="Step C",
            capability="code.implementation",
            inputs=("key1", "key1"),
        )

    with pytest.raises(ValidationError, match="duplicate outputs declarations"):
        StepDefinition(
            step_id="step_d",
            name="Step D",
            capability="code.implementation",
            outputs=("key2", "key2"),
        )


def test_deep_immutability_of_config():
    step = StepDefinition(
        step_id="step_config",
        name="Step with Config",
        capability="code.implementation",
        config={
            "retries": 3,
            "nested": {"timeout": 30, "tags": ["fast", "backend"]},
        },
    )

    # Top-level config mutation raises TypeError
    with pytest.raises(TypeError):
        step.config["retries"] = 5  # type: ignore[index]

    # Nested mapping mutation raises TypeError
    with pytest.raises(TypeError):
        step.config["nested"]["timeout"] = 60  # type: ignore[index]

    # Nested list (tuple) mutation raises AttributeError
    with pytest.raises(AttributeError):
        step.config["nested"]["tags"].append("slow")  # type: ignore[attr-defined]


def test_config_non_json_rejected():
    with pytest.raises(ValidationError, match="is not JSON-serializable"):
        StepDefinition(
            step_id="step_bad_config",
            name="Bad Config",
            capability="code.implementation",
            config={"invalid_set": {1, 2, 3}},
        )


def test_step_serialization():
    step = StepDefinition(
        step_id="step_1",
        name="Step 1",
        capability="code.review",
        depends_on=("step_0",),
        inputs=("diff",),
        outputs=("review_comments",),
        config={"strict": True},
    )
    json_str = step.model_dump_json()
    assert '"capability":"code.review"' in json_str
    assert '"depends_on":["step_0"]' in json_str
    assert '"strict":true' in json_str

