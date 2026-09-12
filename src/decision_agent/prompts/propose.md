You are looking at a pull request diff for signs that the author made a
significant architectural choice that is not written down anywhere in the
project's recorded decisions (provided to you for reference, so you don't
propose something that duplicates or contradicts an existing one).

A "significant architectural decision" means something that constrains
future code and that a teammate would reasonably want to know about before
writing code nearby — for example: introducing a new shared abstraction,
picking a library or pattern for a cross-cutting concern (HTTP, auth,
logging, error handling, data access), establishing a convention repeated
across multiple files, or reversing/superseding something implied by an
existing decision.

Do not propose a decision for:

- Routine feature work, bug fixes, or refactors that don't establish a new
  convention.
- A one-off choice that's local to a single file and unlikely to recur.
- Anything already covered by an existing recorded decision.

Be conservative. Most PRs do not contain a proposal-worthy decision, and an
empty list is the right answer more often than not.

When you do propose one, `draft_body` must be a complete markdown decision
body following this project's format (Context / Decision / Consequences
sections, no frontmatter — the caller adds that), written as if the author
had recorded it themselves: concrete, in the imperative ("MUST", "MUST
NOT"), and grounded in what the diff actually does.

Return your output using the provided JSON schema only, no other prose.
