## ADDED Requirements

### Requirement: Pin the original specification

The system SHALL bind each confirmed baseline to a verified OpenSpec change at a full Git commit in the project's connected public repository.

#### Scenario: Inspect a prior baseline

- **WHEN** a project member opens an older requirement revision
- **THEN** the system displays that revision's Git commit, conditions and recorded evidence

### Requirement: Verify the current acceptance conditions

The system SHALL require independent evidence for each current condition before a quality reviewer can approve the candidate.

#### Scenario: Replace a commit and one condition

- **WHEN** the assigned product owner confirms a new commit with the same OpenSpec change tree and replaces one condition
- **THEN** the replaced condition awaits fresh acceptance and the previous baseline and its evidence remain available
