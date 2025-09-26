"""Command line interface.

The CLI and the HTTP server share `build_runtime`, so both get the same
provider, budget, adapter, and authorization posture. Generation, approval, and
application are separate subcommands on purpose: an approval is a durable record
that a named human made a decision, and applying it is a second, auditable step.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from .adapters import AdapterError, AdapterNotFound, AdapterRateLimited
from .api import serve
from .config import ConfigError, Settings
from .models import Repository, ValidationError
from .runtime import (
    build_runtime,
    default_db_path,
    purge_expired,
    register_repository,
    seed_demo,
)
from .store import Conflict, Expired, NotFound, Store, StoreError, Unauthorized
from .workflows import WorkflowError, proposal_detail, proposal_summary

EXIT_OK = 0
EXIT_ERROR = 2


def cmd_repo_add(args: argparse.Namespace) -> int:
    store = _store(args)
    repository = register_repository(
        store,
        full_name=args.full_name,
        owner_login=args.owner,
        provider=args.provider,
        actor=args.owner,
    )
    _print(
        {
            "registered": repository.as_dict(),
            "settings": store.get_settings(repository.id).as_dict(),
            "next": (
                "Point the forge webhook at /webhooks/github and set "
                "AUTOPILOT_WEBHOOK_SECRET, or run `autopilot triage` against a stored issue."
            ),
        }
    )
    return EXIT_OK


def cmd_repo_list(args: argparse.Namespace) -> int:
    store = _store(args)
    _print({"repositories": store.list_repositories()})
    return EXIT_OK


def cmd_repo_enable(args: argparse.Namespace) -> int:
    store = _store(args)
    store.set_repository_enabled(args.repository_id, not args.disable)
    _print({"repository_id": args.repository_id, "enabled": not args.disable})
    return EXIT_OK


def cmd_repo_delete(args: argparse.Namespace) -> int:
    store = _store(args)
    repository = _require_repository(store, args.repository_id)
    if not args.confirm:
        _print(
            {
                "repository": repository.as_dict(),
                "dry_run": True,
                "next": "re-run with --confirm to delete stored content; the audit trail is kept",
            }
        )
        return EXIT_OK
    counts = store.delete_repository_data(repository.id)
    _print({"deleted": counts, "repository_id": repository.id})
    return EXIT_OK


def cmd_demo_seed(args: argparse.Namespace) -> int:
    store = _store(args)
    repository = seed_demo(store, repository_full_name=args.repository)
    _print(
        {
            "seeded": repository.as_dict(),
            "issues": [issue.number for issue in store.list_issues(repository.id)],
            "note": "local fixtures only; no GitHub token is used",
        }
    )
    return EXIT_OK


def cmd_triage(args: argparse.Namespace) -> int:
    runtime = build_runtime(_settings(args))
    repository = _require_repository(runtime.store, args.repository_id)
    issue = runtime.store.get_issue(repository.id, args.issue)
    if issue is None:
        issue = runtime.adapter.get_issue(repository, args.issue)
        runtime.store.save_issue(issue)
    result = runtime.engine.triage_issue(issue)
    _print(
        {
            "created": result.created,
            "proposal": proposal_detail(result.proposal),
        }
    )
    return EXIT_OK


def cmd_review_pr(args: argparse.Namespace) -> int:
    runtime = build_runtime(_settings(args))
    repository = _require_repository(runtime.store, args.repository_id)
    pull_request = runtime.store.get_pull_request(repository.id, args.pull_request)
    if pull_request is None:
        pull_request = runtime.adapter.get_pull_request(repository, args.pull_request)
        runtime.store.save_pull_request(pull_request)
    result = runtime.engine.review_pull_request(pull_request)
    _print({"created": result.created, "proposal": proposal_detail(result.proposal)})
    return EXIT_OK


def cmd_proposals(args: argparse.Namespace) -> int:
    store = _store(args)
    store.expire_due_proposals()
    proposals = store.list_proposals(
        repository_id=args.repository_id,
        states=[args.state] if args.state else None,
        limit=args.limit,
    )
    _print({"proposals": [proposal_summary(proposal) for proposal in proposals]})
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    store = _store(args)
    store.expire_due_proposals()
    proposal = store.get_proposal(args.proposal_id)
    if proposal is None:
        raise NotFound(f"proposal {args.proposal_id} not found")
    _print(proposal_detail(proposal))
    return EXIT_OK


def cmd_approve(args: argparse.Namespace) -> int:
    runtime = build_runtime(_settings(args))
    proposal = runtime.engine.approve(args.proposal_id, args.actor)
    _print(proposal_summary(proposal))
    return EXIT_OK


def cmd_reject(args: argparse.Namespace) -> int:
    runtime = build_runtime(_settings(args))
    proposal = runtime.engine.reject(args.proposal_id, args.actor, args.reason)
    _print(proposal_summary(proposal))
    return EXIT_OK


def cmd_apply(args: argparse.Namespace) -> int:
    runtime = build_runtime(_settings(args))
    proposal = runtime.engine.execute(args.proposal_id, args.actor)
    _print(proposal_summary(proposal))
    return EXIT_OK if proposal.state == "executed" else 1


def cmd_audit(args: argparse.Namespace) -> int:
    store = _store(args)
    if args.audit_command == "verify":
        ok, checked, broken = store.verify_audit_chain()
        _print({"ok": ok, "checked": checked, "first_broken_id": broken})
        return EXIT_OK if ok else 1
    entries = store.list_audit(repository_id=args.repository_id, limit=args.limit)
    _print({"events": [entry.as_dict() for entry in entries]})
    return EXIT_OK


def cmd_roles_grant(args: argparse.Namespace) -> int:
    store = _store(args)
    store.grant_role(args.repository_id, args.actor, args.role, granted_by=args.granted_by)
    _print({"repository_id": args.repository_id, "actors": store.list_actors(args.repository_id)})
    return EXIT_OK


def cmd_metrics(args: argparse.Namespace) -> int:
    runtime = build_runtime(_settings(args))
    from datetime import UTC, datetime

    month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    since = month.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    summary = runtime.store.cost_summary(since=since, repository_id=args.repository_id)
    _print(
        {
            "model_calls": {
                "requests": summary.requests,
                "cost_usd": round(summary.cost_usd, 6),
                "input_tokens": summary.input_tokens,
                "output_tokens": summary.output_tokens,
                "cache_hits": summary.cache_hits,
            },
            "budget": (
                runtime.gateway.budget_state(
                    args.repository_id, runtime.store.get_settings(args.repository_id)
                ).as_dict()
                if args.repository_id
                else None
            ),
        }
    )
    return EXIT_OK


def cmd_purge(args: argparse.Namespace) -> int:
    runtime = build_runtime(_settings(args))
    counts = purge_expired(store=runtime.store, retention_days=runtime.settings.retention_days)
    _print({"purged": counts, "retention_days": runtime.settings.retention_days})
    return EXIT_OK


def _require_repository(store: Store, repository_id: str) -> Repository:
    repository = store.get_repository(repository_id)
    if repository is None:
        raise NotFound(f"repository {repository_id} not found")
    return repository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autopilot",
        description=(
            "Approval-gated maintainer automation. "
            "Nothing reaches a forge without a human approval."
        ),
    )
    parser.add_argument("--db", help=f"database path (default: {default_db_path()})")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="run the webhook receiver and dashboard")
    serve.add_argument("--host", help="bind address (default from AUTOPILOT_HOST)")
    serve.add_argument("--port", type=int, help="bind port (default from AUTOPILOT_PORT)")
    serve.set_defaults(func=cmd_serve)

    repo = subparsers.add_parser("repo", help="register and inspect repositories")
    repo_sub = repo.add_subparsers(dest="repo_command", required=True)
    add = repo_sub.add_parser("add", help="register a repository and grant owner to a login")
    add.add_argument("full_name", help="owner/name")
    add.add_argument("--owner", required=True, help="login that receives the owner role")
    add.add_argument("--provider", default="github", choices=["github", "gitlab"])
    add.set_defaults(func=cmd_repo_add)
    listing = repo_sub.add_parser("list", help="list registered repositories")
    listing.set_defaults(func=cmd_repo_list)
    toggle = repo_sub.add_parser("enable", help="enable or disable automation for a repository")
    toggle.add_argument("repository_id")
    toggle.add_argument("--disable", action="store_true")
    toggle.set_defaults(func=cmd_repo_enable)
    delete = repo_sub.add_parser(
        "delete", help="delete a repository's stored content, keeping the audit trail"
    )
    delete.add_argument("repository_id")
    delete.add_argument(
        "--confirm", action="store_true", help="actually delete; without it this is a dry run"
    )
    delete.set_defaults(func=cmd_repo_delete)

    demo = subparsers.add_parser("demo", help="local fixture data")
    demo_sub = demo.add_subparsers(dest="demo_command", required=True)
    seed = demo_sub.add_parser("seed", help="seed a deterministic sandbox repository")
    seed.add_argument("--repository", default="demo/autopilot-sandbox")
    seed.set_defaults(func=cmd_demo_seed)

    triage = subparsers.add_parser("triage", help="generate a triage proposal for an issue")
    triage.add_argument("--repo", dest="repository_id", required=True)
    triage.add_argument("--issue", type=int, required=True)
    triage.set_defaults(func=cmd_triage)

    review = subparsers.add_parser(
        "review-pr", help="generate a review proposal for a pull request"
    )
    review.add_argument("--repo", dest="repository_id", required=True)
    review.add_argument("--pr", dest="pull_request", type=int, required=True)
    review.set_defaults(func=cmd_review_pr)

    proposals = subparsers.add_parser("proposals", help="list proposals")
    proposals.add_argument("--repo", dest="repository_id")
    proposals.add_argument("--state")
    proposals.add_argument("--limit", type=int, default=50)
    proposals.set_defaults(func=cmd_proposals)

    show = subparsers.add_parser("show", help="show one proposal as JSON")
    show.add_argument("proposal_id")
    show.set_defaults(func=cmd_show)

    approve = subparsers.add_parser("approve", help="record approval by a named actor")
    approve.add_argument("proposal_id")
    approve.add_argument("--actor", required=True)
    approve.set_defaults(func=cmd_approve)

    reject = subparsers.add_parser("reject", help="record rejection by a named actor")
    reject.add_argument("proposal_id")
    reject.add_argument("--actor", required=True)
    reject.add_argument("--reason", default="")
    reject.set_defaults(func=cmd_reject)

    apply_cmd = subparsers.add_parser("apply", help="apply an approved proposal to the forge")
    apply_cmd.add_argument("proposal_id")
    apply_cmd.add_argument("--actor", required=True)
    apply_cmd.set_defaults(func=cmd_apply)

    audit = subparsers.add_parser("audit", help="inspect the audit chain")
    audit_sub = audit.add_subparsers(dest="audit_command", required=True)
    verify = audit_sub.add_parser("verify", help="verify hash-chain integrity")
    verify.set_defaults(func=cmd_audit)
    events = audit_sub.add_parser("list", help="list audit events")
    events.add_argument("--repo", dest="repository_id")
    events.add_argument("--limit", type=int, default=100)
    events.set_defaults(func=cmd_audit)

    roles = subparsers.add_parser("roles", help="manage repository roles")
    roles_sub = roles.add_subparsers(dest="roles_command", required=True)
    grant = roles_sub.add_parser("grant", help="grant owner, maintainer, or approver")
    grant.add_argument("repository_id")
    grant.add_argument("actor")
    grant.add_argument("role", choices=["owner", "maintainer", "approver"])
    grant.add_argument("--granted-by", default="cli")
    grant.set_defaults(func=cmd_roles_grant)

    metrics = subparsers.add_parser("metrics", help="show model-call spend and budget state")
    metrics.add_argument("--repo", dest="repository_id")
    metrics.set_defaults(func=cmd_metrics)

    purge = subparsers.add_parser("purge", help="apply the retention policy")
    purge.set_defaults(func=cmd_purge)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, ValidationError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Unauthorized as exc:
        print(f"not authorized: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except NotFound as exc:
        print(f"not found: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except (Conflict, Expired) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except AdapterNotFound as exc:
        print(f"not found: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except AdapterRateLimited as exc:
        print(f"rate limited: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except AdapterError as exc:
        print(f"forge error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except (WorkflowError, StoreError) as exc:
        print(f"workflow error: {exc}", file=sys.stderr)
        return EXIT_ERROR


