# Proxmate agent instructions

## Publish meaningful updates to the live dashboard

After verifying a meaningful change to the dashboard, API or worker, build the current workspace with `docker compose build proxmate`. Before restarting, check the dashboard job queue for queued, running or waiting jobs. If any are active, monitor them until the queue is idle so VM builds and script runs can finish.

With the queue idle, run `docker compose up -d --no-build --no-deps --force-recreate --wait proxmate`. Verify the changed behavior through the dashboard’s published port and report that the update is live. For UI changes, include where the user can find the change after reloading an open tab.

## Keep the provisioning guide current

When adding a feature an agent can use to provision machines, or changing an existing provisioning workflow, update [the agent guide](static/llms.txt) in the same change. Explain how to discover the capability, what the caller must supply, which defaults Proxmate chooses, and how the caller can verify the result. Where the API lists available media, settings or tasks, point agents to that discovery endpoint so the guide stays accurate as the lab changes.

Keep the guide aligned with the API handlers and [OpenAPI specification](static/openapi.json). Check for changes to authentication, request and response fields, units, review and confirmation, retry behavior, and job completion. Update the [API reference](static/api.html) and [README](README.md) wherever they describe the affected behavior or link to a page that moved.

When a documented example changes, update its assertions and verify it against the test lab. The Windows example is exercised by `python -m pytest -q tests/test_simple_flow.py::test_agent_guide_example_reviews_without_creating_a_job`, which submits the guide's request for review and checks that the job queue stays empty.
