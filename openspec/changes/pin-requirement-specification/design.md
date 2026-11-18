## Context

Requirement revisions are immutable and group runs already bind a requirement revision. Testing handoffs already identify the development candidate. The project GitHub connector limits reads to the connected public repository.

## Decisions

- Store the verified specification reference on the requirement revision. Resolve full commit and change tree identifiers before saving under the project lock.
- Save the new reference and confirmed conditions together. Compare conditions against the preceding revision. A changed OpenSpec tree can affect all conditions, so report all as affected when their sources cannot be mapped individually.
- Preserve raw evidence for the exact revision and candidate that produced it. Show unchanged conditions with historical evidence separately while a new candidate still needs current proof.
- Require all current conditions to pass in the same independent testing handoff before entering quality review.

## Scope

The pilot provides immutable references and repository links. Interactive OpenSpec exploration and command validation remain separate work within #10.
