# Reviewable, Implementable Specifications

Specifications serve two readers: an owner approving the design and an agent
implementing it. Use progressive disclosure rather than removing important rules
to achieve a shorter document.

```text
Owner overview -> concrete walkthroughs -> detailed contract -> acceptance scenarios
```

## Owner reading path

- Start with a technical overview, roughly 150–250 lines maximum. Explain the
  problem, final system, components and purpose, flow, decisions, trade-offs,
  changes from today, and explicit approval choices.
- Explain unfamiliar domain/technology concepts in 1–3 plain sentences when first
  introduced, specifically describing their use in this project.
- Prefer end-to-end text diagrams and concrete examples over disconnected file lists.
- Provide examples for important flows, including failure, waiting, reset/cleanup,
  and changed inputs where relevant. An owner stopping here should understand the system.
- Link to exact detailed sections instead of repeating their full requirements.
- When a long detailed contract overwhelms the reading path, keep overview and
  walkthroughs in the main spec and move exact rules to a linked companion.
  Include topic/section links and require implementation agents to read both.

## Decision and implementation contract

- Label **DECIDED**, **PROPOSED**, **IMPLEMENTATION DETAIL**, and **OPEN QUESTION**.
  Do not present an agent recommendation as owner approval.
- Group material approval choices visibly. State which stage an open question blocks.
  Delegate low-level choices within constraints instead of making owners review them.
- Put exact behavior, security, failure, concurrency, ownership/cleanup, and edge-case
  rules in a detailed source-of-truth contract with referenceable requirement IDs.
- Make agent discretion concrete: specify the invariants it must preserve and when
  a change requires renewed approval. Do not defer essential architecture or safety
  questions as unspecified implementation details.
- Keep architectural reasoning, implementation procedures, and operational commands
  in their respective spec, implementation-plan, and runbook roles. Avoid duplicated
  requirements or long generic technology explanations.
- Acceptance criteria describe observable input/action/result scenarios and map to
  the contract. State what needs live verification rather than implying mocks prove it.

## Review check before returning a spec

1. Can a senior developer new to the technology understand the architecture in one
   short review (approximately 10–15 minutes) without reading every detail?
2. Can they explain its end-to-end flow after the overview and walkthroughs?
3. Are all material owner approval choices easy to find?
4. Can an implementation agent locate constraints, edge cases, and safe defaults?
5. Does every important flow have a concrete example?
6. Are deeper details available without forcing linear reading?

Restructure if any answer is no. Check local links, decision consistency, and
requirement-to-acceptance coverage. Do not claim a measured reading time unless
someone actually measured it.
