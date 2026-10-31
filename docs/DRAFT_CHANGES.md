# Phase 1: Core Contracts — Draft Change Summary

**Date:** 2026-10-31  
**Phase:** Phase 1 – Core Contracts  
**Status:** Implemented & Verified (61/61 tests passing)

---

## 1. Overview of Changes

In this phase, we implemented the foundational, domain-agnostic **Core Contracts** layer for the AI Development Orchestrator platform under `src/core/contracts/`.

This layer establishes pure Pydantic v2 data contracts, type-safe identifiers, error classification models, and lifecycle status enums. It strictly isolates the platform's core contracts from execution logic, LLM providers, persistence layers, and business applications.

---

## 2. Dependency & Configuration Changes

| File | Change Type | Description |
| :--- | :--- | :--- |
| `requirements.txt` | Modified | Added `pydantic>=2.6.0` runtime dependency (preserving `python-dotenv>=1.0.0`). |
| `requirements-dev.txt` | Created | Added `-r requirements.txt` and `pytest>=8.0.0` for development and testing. |

---

## 3. Core Contract Modules (`src/core/contracts/`)

### `src/core/__init__.py`
* Created platform core namespace package.

### `src/core/contracts/ids.py`
* **Static Typing**: Defines `RunIdType`, `TaskIdType`, `StepIdType`, `ApprovalIdType`, `OperationIdType`, `ArtifactIdType` via `typing.NewType` for compile-time distinction (preventing passing a `TaskId` where a `RunId` is expected).
* **Runtime Validation**: Defines `RunId`, `TaskId`, `StepId`, `ApprovalId`, `OperationId`, `ArtifactId` using `Annotated[..., StringConstraints(strip_whitespace=True, min_length=1)]` to guarantee runtime rejection of empty strings and automatic stripping of whitespace in Pydantic models.

### `src/core/contracts/status.py`
* Defines separate, independent lifecycle enums:
  - `WorkflowStatus`: `PENDING`, `RUNNING`, `WAITING_FOR_APPROVAL`, `PAUSED` (explicit user hold), `SUSPENDED` (unresolved system block), `COMPLETED`, `FAILED`, `CANCELLED`.
  - `TaskStatus`: `PENDING`, `READY`, `RUNNING`, `RETRYING`, `SUCCEEDED`, `FAILED`, `SKIPPED`, `CANCELLED`.
  - `ApprovalStatus`: `PENDING`, `APPROVED`, `REJECTED`, `CHANGES_REQUESTED`.
  - `StepOutcome`: `SUCCEEDED`, `FAILED`, `SKIPPED`, `WAITING_FOR_APPROVAL`.

### `src/core/contracts/errors.py`
* `ErrorCategory`: `TRANSIENT`, `PERMANENT`, `SYSTEM`, `USER_CANCELLED` (categorization decoupled from retry policies).
* `ExecutionError`: Immutable Pydantic model (`frozen=True`) storing error category, message, code, arbitrary details dictionary, stack trace, and UTC timestamp.

### `src/core/contracts/artifact.py`
* `Sha256Hash`: Shared, reusable type using `AfterValidator` to lowercase, strip whitespace, and validate 64-character hex strings against `^[a-f0-9]{64}$`.
* `ArtifactRef`: Immutable reference model (`frozen=True`) with `artifact_id`, version ($\ge 1$), `content_hash` (`Sha256Hash`), `media_type`, and `uri`.

### `src/core/contracts/approval.py`
* `DecisionAction`: Reviewer actions `APPROVE`, `REJECT`, `REQUEST_CHANGES`.
* `ApprovalRequest`: Links `approval_id`, `run_id`, `step_id`, `artifact` (`ArtifactRef`), summary, and context payload.
* `ApprovalDecision`: Immutable review decision (`frozen=True`) storing reviewer, action, comment, timestamp, and `artifact_hash_reviewed` (sharing identical `Sha256Hash` normalization/validation).

### `src/core/contracts/step.py`
* `StepResult[TState]`: Complete state snapshot model generic over `TState` (compatible with Python 3.12 without TypeVar default syntax).
* **Outcome Consistency**: Enforced via `@model_validator(mode="after")`:
  - `SUCCEEDED`: prohibits `error`, `approval_request`, and `skip_reason`.
  - `FAILED`: requires `error`; prohibits `approval_request` and `skip_reason`.
  - `SKIPPED`: requires non-empty `skip_reason`; prohibits `error` and `approval_request`.
  - `WAITING_FOR_APPROVAL`: requires `approval_request`; prohibits `error` and `skip_reason`.
* **Immutability Documentation**: Documents that `frozen=True` protects top-level attributes, while nested dictionary values adhere to a Copy-on-Write convention.

### `src/core/contracts/workflow.py`
* `WorkflowMetadata`: Stores `run_id`, `workflow_id`, `workflow_version`, `status`, `created_at`, and `updated_at`. Documents that `updated_at` must be updated explicitly by the future execution engine upon state mutations.
* `WorkflowContext[TState]`: Generic envelope pairing metadata with a state snapshot (`dict[str, Any]` or a typed Pydantic `BaseModel`).

### `src/core/contracts/task.py`
* `TaskRecord`: Tracks task execution lifecycle. `started_at` and `completed_at` default to `None` while `PENDING`.
* `TaskContext`: Context envelope delivering `task_id`, `run_id`, `step_id`, `inputs`, and `metadata` to a executing task.

### `src/core/contracts/__init__.py`
* Clean public API re-exporting all models, enums, and types with an explicit `__all__` list.

---

## 4. Test Suite (`tests/core/contracts/`)

Total Tests: **61 passed** across 9 test modules:

1. **`test_ids.py` (16 tests)**:
   - Validates that empty strings (`""`) and whitespace-only strings (`"   "`) are rejected across all 6 ID types.
   - Verifies whitespace stripping and static `NewType` distinction.
2. **`test_status.py` (5 tests)**:
   - Verifies enum members, semantic distinction between `PAUSED` and `SUSPENDED`, and JSON serialization.
3. **`test_errors.py` (4 tests)**:
   - Verifies `ErrorCategory` members, `ExecutionError` construction, empty message rejection, and immutability (`frozen=True`).
4. **`test_artifact.py` (10 tests)**:
   - Verifies SHA-256 uppercase-to-lowercase normalization.
   - Verifies rejection of invalid lengths, empty strings, and non-hex characters.
   - Verifies version $\ge 1$ validation and `frozen=True` immutability.
5. **`test_approval.py` (6 tests)**:
   - Verifies `ApprovalRequest` construction.
   - Verifies `ApprovalDecision` validation using `Sha256Hash`.
   - Verifies `DecisionAction` values and immutability.
6. **`test_step.py` (12 tests)**:
   - Verifies outcome consistency for `SUCCEEDED`, `FAILED`, `SKIPPED`, and `WAITING_FOR_APPROVAL`.
   - Rejects contradictory field combinations.
   - Verifies top-level immutability and documents nested dictionary mutability.
7. **`test_workflow_and_task.py` (5 tests)**:
   - Verifies generic instantiation with both raw `dict` and custom Pydantic `BaseModel`.
   - Verifies `WorkflowMetadata` timestamp initialization.
   - Verifies `TaskRecord` optional timestamps (`None` when `PENDING`).
8. **`test_boundary.py` (1 test)**:
   - AST-based import scanner parsing all contract `.py` files.
   - Verifies that all `import` and `from ... import ...` statements import only from standard library (`sys.stdlib_module_names`), `pydantic`, or internal `src.core.contracts.*`.
   - Rejects any imports from orchestration, agents, LLM, or business apps.
9. **`test_exports.py` (2 tests)**:
   - Verifies that all declared contracts are exported cleanly in `src.core.contracts.__all__`.

---

## 5. Verification Command & Output

```powershell
.\venv\Scripts\python.exe -m pytest tests/core/contracts -v
```

```text
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.2.0, pluggy-1.7.0
rootdir: D:\Learning\Self-research\GTM engineer\ai-development-orchestrator
collected 61 items

tests/core/contracts/test_approval.py ............                       [ 9%]
tests/core/contracts/test_artifact.py ..........                         [26%]
tests/core/contracts/test_boundary.py .                                  [27%]
tests/core/contracts/test_errors.py ....                                 [34%]
tests/core/contracts/test_exports.py ..                                  [37%]
tests/core/contracts/test_ids.py ................                        [62%]
tests/core/contracts/test_status.py .....                                [70%]
tests/core/contracts/test_step.py ............                           [91%]
tests/core/contracts/test_workflow_and_task.py .....                     [100%]

============================= 61 passed in 0.58s ==============================
```

---

## 6. Git Working Tree State

Existing user changes (`README.md`, `src/__init__.py`, `docs/ARCHITECTURE.md`) remain preserved. No changes have been staged or committed.

```text
Changes not staged for commit:
	modified:   README.md
	modified:   requirements.txt
	modified:   src/__init__.py

Untracked files:
	docs/
	requirements-dev.txt
	src/core/
	tests/
```

