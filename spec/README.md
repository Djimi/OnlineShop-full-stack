# Specification-driven development

## Migration status
Migration is in progress. Coverage is incomplete; specs are **not yet the
project-wide source of truth**. Read relevant code and existing documentation
alongside specs. Report conflicts and ask before making consequential decisions.
Missing specifications do not mean missing requirements.

Approved change specs define intended behavior, not proof of implementation.
Current specs become authoritative for a covered area only after its baseline
has been checked and explicitly approved. Mark incomplete coverage clearly.
Project-wide specification authority is the migration target, not today's state.

## Where to look
```text
spec/
├── domains/<domain>/spec.md       # Documented current contracts; coverage marked
├── architecture/spec.md          # Current architecture/constraints; coverage marked
├── changes/<feature>/            # Proposed or approved work, not necessarily delivered
│   ├── proposal.md               # Why, goals, scope
│   ├── specification.md          # ADDED/MODIFIED/REMOVED delta + contract links
│   ├── design.md                 # Technical approach and flows
│   ├── plan.md                   # Implementation and verification tasks
│   ├── contracts/                # Canonical resulting contracts when needed
│   └── adr/<decision-name>.md     # Optional significant architectural decisions
├── archive/<seq>_<date>_<id>_<name>/ # Completed changes, immutable
├── templates/                    # Reusable document outlines
├── changelog.md                  # Only global specification change history
└── README.md                     # This workflow
```

Empty domains/change/archive folders are scaffolding, not completed baselines.
Use the files a change needs; combine small prose sections rather than repeat
them. Link instead of duplicating. Keep canonical contract artifacts separate.

## Workflow
```text
Investigate → Propose → Specify → Design → Plan → Human approval
  → Implement + verify → Finalize current specs → Archive + changelog
```

1. **Investigate:** read relevant current specs, architecture, code and existing
   docs. Separate observations from approved requirements. Clarify intent first.
2. **Propose:** record why, goals, scope and exclusions in the active change.
3. **Specify:** show explicit deltas and link complete final contracts for only
   added/changed operations/resources and necessary referenced types. Omit
   unrelated unchanged operations. Use OpenAPI for HTTP, AsyncAPI/payload schemas
   for messages, `.proto` for gRPC, database schemas and structured logging
   contracts when affected. Keep artifacts inside the change folder.
4. **Design:** explain technical flows. Discuss consequential ordering,
   parallelism, retries, timeouts and partial failures before committing to them.
   Add ADRs only for significant architecture, not feedback on unapproved drafts.
5. **Plan:** list implementation and verification tasks with completion evidence.
   Do not imply that an unchecked plan or approved design has been delivered.
6. **Approve:** obtain explicit human approval before implementation or changes
   to approved contracts. No answer means stop; no repeated editorial approvals.
7. **Implement and verify:** follow approved contracts. Report deviations and get
   approval rather than silently changing the design.
8. **Finalize:** update affected current domain/architecture specs to match the
   verified result; preserve explicit gaps elsewhere. Never promote an unbuilt
   feature as current behavior.
9. **Archive and changelog:** complete the serialized procedure below together.

The specification agent stops at an approved handoff; implementation belongs to
an implementation task/agent. Documentation-only restructuring is not delivery
of the features described in those documents.

## Responsibilities
| Document | Content |
|---|---|
| Domain spec | Current business rules, invariants, contract links, acceptance and coverage |
| Architecture spec | Components, boundaries, communication, persistence, security, cross-cutting constraints and coverage |
| Change specification | Delta, affected current-spec links, complete resulting contract links and approval state |
| Design | Implementation approach and technical flows, not a second contract copy |
| Plan | Implementation/verification tasks and evidence |
| ADR | Context, chosen architecture, alternatives, rationale, consequences and approval evidence |
| Archive | Original completed change, including its ADRs and contract artifacts |

ADRs live only in change/archive folders. Do not duplicate them in architecture.
Name them for the feature's architectural decision, not session edits. Historical
ADRs are not automatically current rules: use current specs for documented
constraints and the changelog/archived ADRs to understand past reasoning.
Keep historical approval evidence distinct from current contracts; do not keep
a second evolving approved-proposal/package document. Do not add feature
announcements to AGENTS.md or implemented service guides.

## Archive procedure
Archive only completed, verified and finalized changes. Never modify files after
they have been archived. Do not archive in-progress migration documents.

Name: `<sequence>_<YYYY-MM-DD>_<feature-id>_<short-kebab-name>`.
Example: `0001_2026-05-25_0042_add-wishlist` (illustrative, not an assigned ID).

- Sequence is global archive order, assigned at archive time; date is archive date.
- Feature ID is the story/task identifier, independent of sequence. If it is
  unknown, ask; never invent one from the sequence or an example.
- One archive integration at a time: the operator owns the serialization from
  allocation through merge. Check the latest target branch before allocating;
  if another archive operation is active, wait. Do not allocate in parallel.
- Allocate the next unused sequence, move the complete change folder, and verify
  its relative links before freezing it. Update `spec/changelog.md` in the same
  commit/PR. Resolve sequence collisions before merge, never by editing a frozen
  archive. Archive and changelog must never be published separately.
- Changelog rows are newest first and link to the archived **folder**, not an
  individual file. No per-domain changelogs or duplicate global history.

No archive automation or distributed lock is implemented by this documentation.
The serialized integration rule must also be honored by future automation.
