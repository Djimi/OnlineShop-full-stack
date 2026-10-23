# Script Guidelines

Repository scripts are operational code. A maintainer should be able to open a
script, understand its complete high-level flow first, and then choose which
detail to inspect. Optimizing for this reading path is more important than
minimizing line count or demonstrating abstraction.

## Required reading order

Every non-trivial script must make these layers easy to find, in this order:

1. A brief contract: what the command changes, what it deliberately does not
   change, its important guarantees, and what remains after failure.
2. Constants and small data definitions with explicit names.
3. A short entry point that shows the operation from start to finish.
4. Implementation functions, grouped in the same order as the entry point and
   named after its domain steps.

For example:

```python
def main() -> None:
    request = parse_arguments()
    repository = discover_repository()
    validate_request(request, repository)
    worktree = create_worktree(request, repository)
    allocate_environment(worktree, repository)
    print_next_steps(worktree)
```

The entry point is a table of contents. Generic names such as `process`,
`handle`, or `manager`, when used without a specific object or operation, hide
intent and must be replaced with the concept they represent. Unexplained
prefixes must also be avoided.

## Keep the design direct

- Start with one entry point and one implementation file. Split files only
  when they have independently understandable responsibilities.
- Prefer a direct call to a standard library or command over a project-specific
  wrapper that merely renames it.
- Introduce a helper or abstraction only when it does at least one of these:
  - removes substantial, error-prone repetition;
  - gives a meaningful domain step a clear name;
  - enforces one important invariant or error-handling rule in one place.
- Do not add generic frameworks, layered APIs, registries, modes, or extension
  points for hypothetical future needs.
- Do not preserve legacy commands, compatibility paths, or migration modes
  without a current, documented requirement.
- Do not split an executable and its internals into confusingly similar files.
  File and command names must reveal which one is the entry point.

Some repetition is cheaper than indirection. Keep a repeated operation local
when extracting it would force the reader to jump between files or translate
generic terminology back into the domain.

## Choose the language for the resulting code

Shell is appropriate for short, linear command orchestration. Reconsider it
when the script needs structured data, non-trivial validation, concurrency or
locking, atomic file updates, complex recovery, or many error branches. Python
is the default alternative for repository automation because it keeps those
concerns explicit without adding a build step. Use Go only when distribution
as a standalone binary or Go-specific integration is an actual requirement.

There is no line-count limit. Size is a design signal: if the high-level flow
is hard to find, helpers mostly call other helpers, tests need a large custom
harness, or documentation is explaining indirection instead of behavior, stop
and reassess the scope, language, and abstractions.

## Document intent, not syntax

- The script-level contract explains behavior, guarantees, side effects,
  boundaries, and recovery.
- A function docstring briefly states its responsibility and any guarantee that
  is not obvious from its name or return type.
- Comments explain why an order, invariant, or workaround exists. They do not
  narrate the next line.
- Keep terminology consistent across code, help text, tests, and documentation.

Avoid comments such as `# Increment the slot` above `slot += 1`. A useful
comment would explain why the complete slot is skipped after one occupied port.

## Make tests read as stories

Script tests should describe observable scenarios rather than mirror internal
functions. A reader should see the initial state, the command invocation, and
the expected result without first learning a test framework.

- Name tests after behavior, such as `test_skips_a_slot_claimed_by_a_stopped_worktree`.
- Keep setup explicit and close to the assertion. Extract only genuinely shared
  fixture construction or process execution.
- Prefer focused black-box scenarios for command behavior. Unit-test a helper
  only when it contains meaningful logic that is clearer in isolation.
- Test important failure and recovery paths, invariants, and side effects. Do
  not keep exhaustive tests for removed modes or implementation details.
- A test suite is documentation too: group scenarios in the same order as the
  command's flow and keep failure output easy to diagnose.

## Review checklist

Before accepting a new or changed script, verify:

- Can a new maintainer explain the whole operation after reading the contract
  and entry point?
- Are responsibilities named in domain language, without unexplained prefixes?
- Does each abstraction remove real repetition, name a real step, or enforce a
  real invariant?
- Can any mode, compatibility path, wrapper, or file be removed because no
  current requirement needs it?
- Is the language still the clearest choice for the script's actual complexity?
- Do comments and docstrings explain intent and guarantees rather than syntax?
- Do tests tell a small set of complete behavioral stories, including failure
  and recovery where relevant?
- Do help text and related documentation state the steps, outputs, side effects,
  and limitations precisely?

If several answers are no, simplify the design before adding more explanation.
