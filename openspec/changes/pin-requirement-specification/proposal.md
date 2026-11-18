## Why

MyAgentDesk pilot requirements need a reproducible specification and acceptance baseline when the repository moves. A tester and reviewer must be able to open the version used by an earlier run.

## What Changes

- Bind a requirement revision to an approved public repository, an OpenSpec change directory and a full Git commit SHA.
- Version confirmed acceptance conditions. Record independent evidence per condition for the current development handoff.
- Show specification sources, conditions and evidence for current and historical revisions from the project and requirement.

## Capabilities

### New Capabilities

- `requirement-specification`: Fixed OpenSpec references and versioned acceptance evidence.

## Impact

Project specifications, requirement groups, Pi SDK testing handoffs and quality review use the same requirement revision.
