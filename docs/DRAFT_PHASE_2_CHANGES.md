# Phase 2: Workflow Definition & State Model — Draft Change Summary

**Date:** 2026-10-31  
**Phase:** Phase 2 – Workflow Definition & State Model (including Targeted Review Fixes)  
**Status:** Implemented & Verified (161/161 total tests passing: 61 Phase 1 + 100 Phase 2)

---

## 1. Overview of Changes

In Phase 2, we implemented the declarative, agent-agnostic **Workflow Definition, DAG Compilation, and State Model** layer under `src/core/workflow/`.

This layer enables authoring declarative workflow templates, enforcing compile-time DAG and data contract validation, generating deterministic topological execution plans, and defining a runtime state snapshot container. It remains strictly domain- and agent-agnostic, with zero coupling to specific AI models, agent personas, or runtime execution engines.

---

## 2. Implemented Modules (`src/core/workflow/`)

| File | Primary Role & Responsibilities |
| :--- | :--- |
| [`src/core/workflow/freezer.py`](file:///d:/Learning/Self-research/GTM%20engineer/ai-development-orchestrator/src/core/workflow/freezer.py) | **Strict JSON validation & deep immutability**: Enforces allowed primitives (`None`, `bool`, `int`, finite `float`, `str`, `list`, `tuple`, `dict`), rejects non-finite floats (`NaN`/`Inf`), non-string dict keys, non-JSON types, and circular references. Recursively freezes mappings into `types.MappingProxyType` and lists into `tuple`. |
| [`src/core/workflow/step_definition.py`](file:///d:/Learning/Self-research/GTM%20engineer/ai-development-orchestrator/src/core/workflow/step_definition.py) | **Agent-agnostic step specification**: Enforces dot-notation `Capability` format (e.g. `requirement.analysis`, `quality.assurance`) with strict validation (no `strip_whitespace=True`, rejecting leading/trailing/embedded whitespace without normalization). Rejects self-dependencies, duplicate dependencies, and duplicate input/output declarations. Freezes `config` deeply into `Mapping[str, Any]` (`MappingProxyType`). |
| [`src/core/workflow/definition.py`](file:///d:/Learning/Self-research/GTM%20engineer/ai-development-orchestrator/src/core/workflow/definition.py) | **Declarative workflow template**: Defines `WorkflowInputDefinition` (with `required` flag) and `WorkflowDefinition`. Enforces strict SemVer 2.0.0 via `SEMVER_REGEX.fullmatch(v)` (rejecting surrounding whitespace, trailing newlines like `1.0.0\n`, and leading zeros). Rejects duplicate input keys and duplicate step IDs. Freezes `metadata`. |
| [`src/core/workflow/hashing.py`](file:///d:/Learning/Self-research/GTM%20engineer/ai-development-orchestrator/src/core/workflow/hashing.py) | **Deterministic canonical SHA-256 hashing**: Implements `compute_canonical_workflow_hash()`. Canonicalizes all semantic fields (`workflow_id`, `version`, `name`, `description`, `state_schema`, sorted `metadata`, sorted `inputs`, and sorted `steps`). Uses compact JSON (`separators=(',', ':')`, `ensure_ascii=False`, `allow_nan=False`) and returns Phase 1 [`Sha256Hash`](file:///d:/Learning/Self-research/GTM%20engineer/ai-development-orchestrator/src/core/contracts/artifact.py#L21-L25). |
| [`src/core/workflow/plan.py`](file:///d:/Learning/Self-research/GTM%20engineer/ai-development-orchestrator/src/core/workflow/plan.py) | **Compiled execution plan & stage groups**: Defines `ExecutionStage` and `ExecutionPlan`. Enforces all 6 integrity invariants: contiguous stage indexing, exact ordering consistency (`flatten(execution_stages) == topological_order`), deterministic alphabetical sorting within stages, dependency mapping completeness (`set(step_dependencies.keys()) == set(topological_order)`), dependency precedence, and deep immutability of `step_dependencies` via `MappingProxyType`. |
| [`src/core/workflow/state.py`](file:///d:/Learning/Self-research/GTM%20engineer/ai-development-orchestrator/src/core/workflow/state.py) | **Runtime state snapshot container**: Minimal `WorkflowState[TState]` container bound to `BaseModel | dict[str, Any]` (identical to Phase 1 [`WorkflowContext[TState]`](file:///d:/Learning/Self-research/GTM%20engineer/ai-development-orchestrator/src/core/contracts/workflow.py#L27-L31)). Documented outer immutability and inner Copy-on-Write convention. |
| [`src/core/workflow/errors.py`](file:///d:/Learning/Self-research/GTM%20engineer/ai-development-orchestrator/src/core/workflow/errors.py) | **Domain validation exceptions**: Hierarchical errors including `CircularDependencyError` (with cycle path), `UnknownDependencyError`, `SelfDependencyError`, `DuplicateStepIdError`, `DuplicateOutputProducerError`, `OutputOverwritesInputError`, `UnsatisfiedStateInputError`, and `OptionalInputAsRequiredDependencyError`. |
| [`src/core/workflow/compiler.py`](file:///d:/Learning/Self-research/GTM%20engineer/ai-development-orchestrator/src/core/workflow/compiler.py) | **Deterministic DAG compiler**: Verifies referential integrity, detects cycles with readable paths (`A -> B -> C -> A`), validates data contract semantics (single producer, ancestor visibility, optional input restriction), and executes Kahn's algorithm with lexicographical tie-breaking to compile an `ExecutionPlan`. |
| [`src/core/workflow/__init__.py`](file:///d:/Learning/Self-research/GTM%20engineer/ai-development-orchestrator/src/core/workflow/__init__.py) | **Public package exports**: Clean public API surface exporting all models, compiler, and errors with an explicit `__all__` list without exposing internal helpers (`freezer`, `_check_cycles`, etc.). |

---

## 3. Targeted Review Fixes Summary

1. **Strict Capability Validation (`src/core/workflow/step_definition.py`)**:
   - Removed `strip_whitespace=True` from `Capability` Pydantic `StringConstraints`.
   - Capabilities are rejected if they contain leading, trailing, or internal whitespace.
   - Preserves lowercase dot-separated capability format without silent normalization.
   - Added specific tests verifying:
     - `"requirement.analysis"` $\to$ accepted.
     - `" requirement.analysis"` $\to$ rejected.
     - `"requirement.analysis "` $\to$ rejected.
     - `"requirement. analysis"` $\to$ rejected.

2. **Strict SemVer Validation (`src/core/workflow/definition.py`)**:
   - Replaced `SEMVER_REGEX.match(v)` with `SEMVER_REGEX.fullmatch(v)`.
   - Preserves SemVer 2.0.0 compliance while strictly rejecting trailing newlines (`"1.0.0\n"`), leading whitespace (`" 1.0.0"`), and trailing whitespace (`"1.0.0 "`).
   - Added specific tests verifying each of these cases.

3. **Workflow Public Exports & Dedicated Test (`src/core/workflow/__init__.py`, `tests/core/workflow/test_exports.py`)**:
   - Clean public export surface with explicit `__all__` list (19 symbols).
   - Dedicated test `tests/core/workflow/test_exports.py` verifying `__all__` exists, no duplicate entries, all items in `__all__` are importable, and internal helpers (`freeze_value`, `unfreeze_value`, `validate_json_serializable`, `_check_cycles`) are excluded.

---

## 4. Test Suite Breakdown (`tests/core/workflow/`)

Total Phase 2 Tests: **100 passed** across 9 test modules:

1. **`test_freezer.py` (15 tests)**:
   - Validates JSON primitives and nested structures.
   - Rejects non-finite floats (`nan`, `inf`, `-inf`).
   - Rejects non-string dictionary keys (`{1: "a"}`, `{(1, 2): "b"}`).
   - Rejects non-JSON types (`set`, `bytes`, `datetime`, custom objects).
   - Rejects direct and indirect circular references.
   - Verifies deep freezing into `MappingProxyType` and `tuple`, testing that nested mutations raise `TypeError` or `AttributeError`.
2. **`test_step_definition.py` (22 tests)**:
   - Validates standard and multi-segment capabilities (`requirement.analysis`, `pipeline.deploy.prod`).
   - Rejects invalid capabilities (no dot, uppercase, spaces, leading/trailing/consecutive dots).
   - Rejects leading, trailing, and embedded whitespace on capabilities.
   - Rejects self-dependencies.
   - Rejects duplicate `depends_on`, `inputs`, and `outputs` within the same step.
   - Verifies `config` deep immutability and JSON serialization.
3. **`test_workflow_definition.py` (23 tests)**:
   - Verifies SemVer 2.0.0 regex compliance (accepts `1.0.0`, `0.1.0-alpha.1`, `1.0.0+build.123`).
   - Rejects surrounding whitespace, leading zeros (`01.0.0`, `1.0.0-01`), and trailing newlines (`1.0.0\n`).
   - Rejects duplicate workflow input keys and duplicate step IDs.
   - Verifies `metadata` deep immutability.
   - Verifies canonical hash sensitivity (descriptions, version, metadata, config) and invariance under step/dependency permutation.
4. **`test_plan_integrity.py` (10 tests)**:
   - Validates correct `ExecutionPlan` creation.
   - Rejects duplicate steps, total step count mismatches, non-contiguous stage indices, and unsorted steps within a stage.
   - Enforces `flatten(execution_stages) == topological_order`.
   - Enforces dependency mapping completeness and rejects missing or unknown steps.
   - Rejects dependency inversion across stages or topological order.
   - Verifies top-level and nested immutability on `step_dependencies`.
5. **`test_compiler.py` (7 tests)**:
   - Verifies linear DAG compilation (`A -> B -> C`).
   - Verifies diamond DAG stage grouping (`A -> (B, C) -> D`).
   - Verifies disconnected subgraphs and lexicographical tie-breaking.
   - Proves compiler determinism across 20 randomized step shuffling iterations.
   - Verifies cycle detection with readable cycle path reporting (`A -> B -> C -> A`).
   - Rejects unknown dependencies.
6. **`test_data_contracts.py` (9 tests)**:
   - Rejects multiple producers for the same output key (`DuplicateOutputProducerError`).
   - Rejects step outputs colliding with required or optional workflow inputs (`OutputOverwritesInputError`).
   - Verifies all 4 optional input semantics cases:
     - Case 1: Consuming required workflow input $\to$ **Passes**.
     - Case 2: Consuming optional workflow input $\to$ **Raises `OptionalInputAsRequiredDependencyError`**.
     - Case 3: Unused optional workflow input $\to$ **Passes**.
     - Case 4: Unused required workflow input $\to$ **Passes**.
   - Verifies ancestor output visibility (ancestor outputs allowed, downstream/concurrent outputs rejected).
7. **`test_workflow_state.py` (4 tests)**:
   - Tests `WorkflowState` generic instantiation with raw `dict` and Pydantic `BaseModel`.
   - Verifies outer model frozen behavior and documents inner Copy-on-Write convention.
8. **`test_boundary.py` (1 test)**:
   - AST boundary test confirming `src/core/workflow/` imports only standard library, Pydantic, and `src.core.contracts.*`. Rejects any imports from orchestration, agents, LLM, or business apps.
9. **`test_exports.py` (2 tests)**:
   - Verifies `__all__` completeness, integrity, and lack of duplicates on `src.core.workflow`.
   - Verifies internal helpers are not leaked into the public namespace.

---

## 5. Verification Results

```powershell
.\venv\Scripts\python.exe -m pytest -v
```

```text
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.2.0, pluggy-1.7.0 -- D:\Learning\Self-research\GTM engineer\ai-development-orchestrator\venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: D:\Learning\Self-research\GTM engineer\ai-development-orchestrator
collecting ... collected 161 items

tests/core/contracts/test_approval.py ............                        [  7%]
tests/core/contracts/test_artifact.py ..........                          [ 14%]
tests/core/contracts/test_boundary.py .                                   [ 15%]
tests/core/contracts/test_errors.py ....                                  [ 17%]
tests/core/contracts/test_exports.py ..                                   [ 19%]
tests/core/contracts/test_ids.py ................                         [ 29%]
tests/core/contracts/test_status.py .....                                 [ 33%]
tests/core/contracts/test_step.py ............                            [ 41%]
tests/core/contracts/test_workflow_and_task.py .....                      [ 44%]
tests/core/workflow/test_boundary.py .                                    [ 45%]
tests/core/workflow/test_compiler.py .......                              [ 50%]
tests/core/workflow/test_data_contracts.py .........                      [ 56%]
tests/core/workflow/test_exports.py ..                                    [ 57%]
tests/core/workflow/test_freezer.py ...............                       [ 66%]
tests/core/workflow/test_plan_integrity.py ..........                     [ 72%]
tests/core/workflow/test_step_definition.py ......................        [ 86%]
tests/core/workflow/test_workflow_definition.py .......................   [100%]
tests/core/workflow/test_workflow_state.py ....                           [100%]

============================= 161 passed in 0.76s =============================
```

---

## 6. Git Working Tree State

```text
On branch master
Untracked files:
  (use "git add <file>..." to include in what will be committed)
	docs/DRAFT_PHASE_2_CHANGES.md
	src/core/workflow/
	tests/core/workflow/
```
