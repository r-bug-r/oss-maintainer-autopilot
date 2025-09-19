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


