---
name: codebase-quality
description: Audit a repository's architecture, implement selected behavior-preserving improvements, or review an ordinary feature for locality, explicit contracts, coupling and regression risk. Use Audit, Improve or Continuous according to the requested scope.
---

# Codebase quality

Read the applicable repository instructions. Choose the mode from the request. For Proxmate, start with the relevant entry points in the [architecture audit](../../../docs/architecture-audit.md) and confirm them in the current source.

## Audit

Leave application code untouched. Inventory first-party code, dependencies, data models, runtime processes and available test tools. Trace representative public requests through the browser or API, validation, persistence, workers and integrations. Include an error, retry or restart path. State inspection limits.

For each significant finding, give the file and symbol, observed behavior, consequence, smallest defensible improvement, expected benefit, risk, verification needed and confidence. Prioritize by impact, change frequency, effort and strength of evidence. Explain which boundaries should survive. Report measured timings only if actually collected.

## Improve

Choose a small group of findings with a clear payoff. Identify callers, persisted data, side effects and externally observable behavior. Capture representative current outputs or failure cases before risky moves. Start with proven dead code and unnecessary forwarding, then address cohesion, dependency direction and types where justified.

Make one logical change at a time. Trace the normal entry point again. Run focused regressions before broader tests, type checks, lint or image checks provided by the repository. Review the diff and report limitations. Seek approval before changing public behavior, contracts, formats, schemas or security guarantees during an architectural refactor. Compare a concrete workflow before and after, such as which modules a reader must inspect or which dependencies an isolated test needs.

## Continuous

Before an ordinary feature or fix, find its owner, tests and invariants. While coding, keep related behavior local and simplify touched code where the task provides evidence. Keep unrelated redesigns in the backlog. Afterward, verify the behavior through a real caller and review the following questions.

## Review questions

- Does each module hide a complete responsibility behind a narrow interface? What does each wrapper protect, for example compatibility, a transaction, trust, caching or diagnostics?
- Can a reader follow one feature without tracing unrelated modules? Do responsibility and dependency direction agree? Check deferred imports, cycles, mutable globals and initialization order.
- Are dependencies, data shapes, units, defaults and error outcomes explicit at the boundary? Can concrete types prevent a real invalid state? Can ordinary control flow replace dynamic dispatch or speculative indirection?
- Is each critical invariant authoritative in one place? Do similar-looking cases actually share the same policy? Permit limited duplication when combining them would create coupling.
- Can pure decisions run independently of network, clock, filesystem and persistence? Keep necessary side effects visible and preserve ordering, locks and atomicity.
- Can a test observe success, failure, old data and applicable retry or concurrency behavior? Are failures actionable while credentials and sensitive output stay private?
- Which concepts must the next maintainer understand? Keep the simpler design even if it takes more lines. Describe remaining risks and unverified assumptions.
