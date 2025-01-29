# Governance

## Model

This is a maintainer-led project. Decisions are made in public issues and pull
requests, and the rationale for anything that constrains future options is
recorded in `docs/decisions/` with the date it was made.

## Roles

| Role | Who | Powers |
| --- | --- | --- |
| Maintainer | listed below | merge, release, change governance, change security posture |
| Reviewer | anyone with a sustained record of correct review | approve pull requests, no merge rights |
| Contributor | anyone | open issues, send pull requests |

The project is new; the maintainer list is intentionally short. Additional
maintainers are added after a sustained record of reviewed contributions and
explicit agreement from the existing maintainer.

Current maintainer: the repository owner (`@` handle recorded in the repository
settings). No other maintainers are appointed yet.

## Decision process

1. Open an issue describing the problem, not only the proposed solution.
2. For changes that touch approval, authorization, audit, cost controls, or data
   retention, write a decision record in `docs/decisions/YYYY-MM-DD-<slug>.md`
   before implementation. Include the alternatives considered and why they lost.
3. Lazy consensus: if no maintainer objects within seven days, a proposal with
   an implementation and tests may be merged by a maintainer.
4. Security fixes may skip the waiting period but still get a decision record
   after disclosure.

## What requires a decision record

- Changing the approval or authorization model.
- Adding a network dependency, hosted service, or paid provider.
- Changing default retention, redaction, or logging behavior.
- Changing the webhook trust model.
- Declaring a project phase complete.

## Releases

`0.x` releases are cut from `main` when the changelog has an entry for the
version and all checks pass. Release notes are generated from merged pull
requests; the release workflow only runs on a tag.

## Code of conduct

Enforced per `CODE_OF_CONDUCT.md`. Maintainers handle reports privately.
