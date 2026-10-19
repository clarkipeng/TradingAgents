# Engineering Taste Canon

Status: canonical distillation prepared 2026-08-31 from the current worktrees named in the task.

This document describes the owner's engineering taste as observable rules with repository evidence.

## Invariants over edge cases

Prefer a small invariant that makes unusual cases correct by construction over a branch for each unusual case.

The `catapult-games` documentation makes authoritative state, player views, legal actions, and replay boundaries explicit, while `p2c2` validates complete proof contracts before finalization.

Name the invariant first, then make code, tests, and docs enforce it.

## Smallest robust architecture

Choose the fewest components that preserve the contract and remove rejected alternatives instead of leaving dormant paths.

The FL spool investigation retired file responses after live testing showed that the controller interpreter could read but not write, then simplified the working design to server-hosted MIDI without a bridge process.

The Catapult architecture likewise assigns each boundary one responsibility and explicitly excludes model gateways, production bots, and dynamic game deployment from the runtime map.

Prefer root-cause fixes over compatibility shims or special-case recovery code.

## Fail-closed validation at schema boundaries

Validate exact shapes, types, ranges, identities, and canonical encodings before data crosses a process, persistence, execution, or trust boundary.

`catapult-runner` parses only the exact canonical proof policy shape, rejects extra or missing fields through canonical re-serialization, rejects unsafe relative paths and overlapping mounts, and accepts an isolation attestation only when every required enforcement fact is present.

`p2c2` rejects malformed, duplicate, missing, shuffled, constant, and unexpected proof outputs through fixture-backed contract tests.

The FL representation layer validates every typed object through Pydantic models before projecting it to MCP, spool, or disk formats.

A missing, ambiguous, stale, or unverifiable claim is a refusal, not a best-effort success.

## Derived views over duplicated state

Keep one authoritative representation and derive compact views, indexes, hashes, projections, and reports from it.

FL's representation architecture makes `schemas.py` the single definition and derives content IDs, compressed model views, structured diffs, and JSON encodings from those models.

Catapult ratings are derived from immutable ordered match records, and the game catalog is derived from checked-in manifests rather than a second game list.

When an external system cannot expose its state, retain the smallest explicitly named observation or audit record and declare its freshness and authority.

The older FL interface spec calls this record the shadow score because FL cannot read notes, while the newer representation design improves it with content hashes, provenance, and diffs against fresh project parses.

## One owner per lifecycle

Assign creation, mutation, validation, persistence, teardown, and recovery to one named owner at each lifecycle boundary.

Catapult assigns room semantics to `room-runtime`, persistence and transport recovery to host adapters, and hosted composition to the Play Shell adapter.

`catapult-runner` gives `LifecycleMonitor` ownership of command markers and events, and keeps process-group termination in lifecycle helpers.

The runner's postprocess grace behavior is an example of preserving the command lifecycle until terminal evidence is observed rather than killing a process on an unrelated runtime timeout.

## Tests as executable invariants named after behavior

Write focused tests whose names state the behavior and whose assertions prove the invariant at the boundary where it matters.

Runner tests use names such as `test_download_digest_mismatch_leaves_no_claimed_input`, `test_declared_checks_without_sources_fail_closed`, and `test_coding_agent_missed_command`.

P2C2 tests name method fidelity and outcome behavior, including `test_shortcut_grades_outcome_met_but_gamed` and `test_faithful_result_grades_reproduced`.

Catapult's authoring guide requires deterministic tests for mechanics, termination, information boundaries, and privacy risk.

Run only the narrowest check that can catch a plausible regression, then expand checks when the changed invariant crosses a package or delivery boundary.

## No legacy compatibility paths

When a contract is superseded, remove the old vocabulary and behavior after verifying active consumers and durable data, rather than preserving a permanent compatibility branch.

Catapult documentation says to delete superseded wording and requires a live reference and data audit before destructive schema retirement.

The FL spool verdict keeps the failed spool artifacts as historical evidence but removes the bridge from the working architecture and marks the spool as non-viable as the sole transport.

Compatibility is justified only by an identified live consumer or retained data requirement, and the owner and removal condition must be explicit.

## Docs updated with the invariant they describe

Put each durable invariant in the narrowest owning document and link to it rather than copying inventories or chronology across documents.

`catapult-games/docs/README.md` maps product invariants to `SPEC.md`, runtime boundaries to `ARCHITECTURE.md`, and delivery policy to `CI_POLICY.md`, while `INDEX.md` is generated.

FL's documentation index points readers to the plain guide, protocol spec, and representation architecture, and the live spool verdict records both the observed failure and the corrected implementation.

Lead with current behavior, and keep chronology, raw investigation notes, and transient evidence in Git history, pull requests, CI artifacts, or `.context`.

## Writing and change shape

Use short semantic instructions, plain dashes, sentence-per-line Markdown, and no agent co-authors.

The global Claude rules require sentence-per-line Markdown for long documents, forbid the em dash, and forbid automatic agent co-authors.

Keep implementation, tests, user-visible copy, owning docs, and guardrails synchronized when an invariant changes.

Prefer existing utilities and established types before adding a new abstraction.

## Genuine tensions and scope boundaries

The FL docs contain a temporal and scope tension rather than a single consistent vocabulary.

The older `docs/11-interface-spec.md` and `docs/02-architecture.md` describe FL Studio as the truth and the server's shadow score as a retained derived record, while `docs/16-representation-architecture.md` calls Pydantic models the single source of truth for the server's internal representation.

These statements are compatible only when "truth" means external host state and "single source" means the validated internal model, so future docs must name that boundary explicitly.

The older FL kernel findings also use "backward-compatible" additive extensions, which conflicts with the canon's default of removing superseded paths; treat those extensions as a dated migration exception with an owner, not as a general style rule.

The Catapult game contract says expected invalid gameplay actions should be rejected without throwing, while runner policy parsers raise typed errors for invalid control-plane documents.

This is an intentional boundary distinction between user gameplay input and trusted execution configuration, not a contradiction in failure posture.

## Source assessment and permanent home

The most current explicit taste sources are the global `/Users/clarkpeng/.claude/CLAUDE.md`, `catapult-games/AGENTS.md`, Catapult's `docs/README.md`, `docs/SPEC.md`, and `docs/ARCHITECTURE.md`, plus FL's `docs/16-representation-architecture.md` and the 2026-08-26 `docs/14-spool-first-verdict.md`.

They are understandable when read through their owning-document links and concrete examples, and the Catapult set is especially clear about authority, projections, lifecycle boundaries, and verification.

The FL set is current in its newest implementation notes but uneven as a reader experience because older planning documents remain prominent, some describe future proposals as if they were system shape, and the shadow-score versus model-source vocabulary is not yet normalized.

What is missing is one short cross-repo statement of engineering taste, an explicit precedence rule for global guidance versus repo guidance versus design records, freshness metadata on the FL documents, and a clear distinction between external truth, internal canonical models, and derived observations.

The permanent canonical home should be a small owner-maintained document at `/Users/clarkpeng/.claude/engineering-taste.md`, referenced by `/Users/clarkpeng/.claude/CLAUDE.md` and each repository's `AGENTS.md`.

This checked-in copy at `.context/handoffs/fleet/taste-canon.md` is the fleet handoff and evidence-backed snapshot, not a substitute for that stable global home.

## Report

The existing taste sources are substantially up to date through 2026-08-27 in the inspected repositories, with Catapult's explicit policy last committed 2026-08-13 and newer implementation-specific design records continuing afterward.

They are understandable overall, but FL needs cleanup of superseded planning language and explicit authority labels, while Catapult needs only the cross-repo bridge supplied by this document.

The missing canonical layer is the cross-repo precedence and vocabulary described above, especially the distinction between a host-owned external truth and a model-owned internal canonical representation.

The recommended permanent location is `/Users/clarkpeng/.claude/engineering-taste.md`, with this handoff linked from it and repo-specific `AGENTS.md` files containing only actionable local rules.
