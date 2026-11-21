# Proxmate agent instructions

## Publish meaningful updates to the live dashboard

After verifying a meaningful change to the dashboard, API or worker, build the current workspace with `docker compose build proxmate`. Before restarting, check the dashboard job queue for queued, running or waiting jobs. If any are active, monitor them until the queue is idle so VM builds and script runs can finish.

With the queue idle, run `docker compose up -d --no-build --no-deps --force-recreate --wait proxmate`. Verify the changed behavior through the dashboard’s published port and report that the update is live. For UI changes, include where the user can find the change after reloading an open tab.

## Keep the provisioning guide current

When adding a feature an agent can use to provision machines, or changing an existing provisioning workflow, update [the agent guide](static/llms.txt) in the same change. Explain how to discover the capability, what the caller must supply, which defaults Proxmate chooses, and how the caller can verify the result. Where the API lists available media, settings or tasks, point agents to that discovery endpoint so the guide stays accurate as the lab changes.

Keep the guide aligned with the API handlers and [OpenAPI specification](static/openapi.json). Check for changes to authentication, request and response fields, units, review and confirmation, retry behavior, and job completion. Update the [API reference](static/api.html) and [README](README.md) wherever they describe the affected behavior or link to a page that moved.

When a documented example changes, update its assertions and verify it against the test lab. The Windows example is exercised by `python -m pytest -q tests/test_simple_flow.py::test_agent_guide_example_reviews_without_creating_a_job`, which submits the guide's request for review and checks that the job queue stays empty.

Treat published lab packs and versioned built-in task references as compatibility contracts. Version 1 creates new guests. In-place task roles require version 2, an explicit local target, and separate approval. Keep authoring free of infrastructure changes and login secrets. When changing portable settings, script resolution or deployment behavior, update [the pack specification](static/lab-packs.md) and run `python -m pytest -q tests -k lab`. Verify that an archive still works on a second dashboard with an empty script library and fresh credentials.

## Keep changes easy to reason about

- Before coding, trace one real caller through validation, storage and execution. Identify the owning module, the contract it promises and a focused test. Use the [codebase-quality skill](.agents/skills/codebase-quality/SKILL.md) in Continuous mode for routine work, Audit for assessment and Improve for selected refactors.
- Keep a complete responsibility behind a narrow interface. Put decisions, validation and types near their callers when they change together. Make dependencies and side effects visible. Prefer straightforward control flow and measure simplicity by what a reader must understand.
- Centralize rules that protect machine identity, secrets, reviewed plans and transactional job claims. A small amount of local duplication is fine when the policies can evolve independently. Introduce an abstraction when it owns a real shared contract.
- Preserve older stored records, retry behavior, trust checks and the review-to-execution boundary. Characterize risky behavior first. Get approval for changes to observable behavior, APIs, persisted formats, schemas or security guarantees during a refactor.
- Before finishing, review the diff for shallow wrappers, hidden dependencies, duplicate rules and unrelated changes. Exercise normal and failure paths through the public entry point and run the relevant suites. Record what was verified and any limits.
