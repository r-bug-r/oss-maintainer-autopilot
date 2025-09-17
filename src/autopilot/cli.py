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


