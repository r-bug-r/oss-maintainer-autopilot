"""Workflow orchestration: proposal creation and gated execution.

The invariant this module protects: nothing leaves the process toward a forge
unless a stored proposal reached `approved` through an explicit human action.
Generation and execution are separate calls, and execution is claimed with a
single conditional UPDATE so a repeated click cannot post twice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from .adapters import (
    AdapterAuthError,
    AdapterError,
    AdapterNotFound,
    AdapterRateLimited,
    AdapterTransient,
    RepositoryAdapter,
)
from .canonical import canonical_json, hash_canonical
from .duplicates import find_candidates
from .gateway import BudgetExceeded, ModelGateway
from .models import (
    ActionType,
    Citation,
    Issue,
    Proposal,
    PullRequest,
    Repository,
    RepositorySettings,
    ValidationError,
    WorkflowName,
)
from .prompts import build_prompt, injection_signals
from .providers import ModelRequest, ProviderError
from .redaction import redact, redact_value
from .schemas import (
    IssueTriageOutput,
    citations_for,
    output_as_payload,
    parse_workflow_output,
)
from .store import Conflict, Expired, NotFound, Store, Unauthorized, new_id

SubjectType = Literal["issue", "pull_request", "repository"]


class WorkflowError(Exception):
    """A workflow could not produce a proposal."""


class WorkflowDisabled(WorkflowError):
    pass


class _ExistingProposal(Exception):
    """Internal signal: this exact input already produced a proposal."""

    def __init__(self, proposal: Proposal) -> None:
        super().__init__(proposal.id)
        self.proposal = proposal


@dataclass(frozen=True, slots=True)
class GenerationResult:
    proposal: Proposal
    created: bool


def _iso_after(minutes: int) -> str:
    moment = datetime.now(UTC) + timedelta(minutes=minutes)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class WorkflowEngine:
    """Turns normalized events into proposals, and proposals into actions."""

    def __init__(
        self,
        *,
        store: Store,
        gateway: ModelGateway,
        adapter: RepositoryAdapter,
        settings: RepositorySettings | None = None,
        proposal_ttl_minutes: int = 60,
        created_by: str = "autopilot",
    ) -> None:
        self.store = store
        self.gateway = gateway
        self.adapter = adapter
        self._default_settings = settings
        self.proposal_ttl_minutes = proposal_ttl_minutes
        self.created_by = created_by

    # ------------------------------------------------------------- settings
    def settings_for(self, repository_id: str) -> RepositorySettings:
        if self._default_settings is not None:
            return self._default_settings
        return self.store.get_settings(repository_id)

    # ----------------------------------------------------------- generation
    def triage_issue(self, issue: Issue) -> GenerationResult:
        repository = self._require_repository(issue.repository_id)
        settings = self.settings_for(repository.id)
        self._require_enabled(settings, "issue_triage")

        others = [
            (item.number, item.title, item.body)
            for item in self.store.list_issues(repository.id, limit=100)
        ]
        candidates = find_candidates(
            subject_number=issue.number,
            subject_title=issue.title,
            subject_body=issue.body,
            others=others,
        )
        context = {
            "workflow": "issue_triage",
            "repository": repository.full_name,
            "number": issue.number,
            "title": issue.title,
            "body": issue.body,
            "labels": list(issue.labels),
            "author": issue.author_login,
            "label_taxonomy": list(settings.label_taxonomy),
            "duplicate_candidates": [
                {"number": candidate.number, "title": candidate.title, "score": candidate.score}
                for candidate in candidates
            ],
        }
        try:
            output, proposal_base = self._generate(
                repository=repository,
                settings=settings,
                workflow="issue_triage",
                subject_type="issue",
                subject_number=issue.number,
                context=context,
                candidate_numbers=tuple(candidate.number for candidate in candidates),
                input_sha256=issue.input_sha256(),
            )
        except _ExistingProposal as existing:
            return GenerationResult(proposal=existing.proposal, created=False)
        assert isinstance(output, IssueTriageOutput)

        labels = list(output.labels)
        payload = output_as_payload(output)
        return self._store_proposal(
            repository=repository,
            workflow="issue_triage",
            subject_type="issue",
            subject_number=issue.number,
            action_type="comment" if payload.get("proposed_comment") else "none",
            action_payload={
                "comment": payload.get("proposed_comment"),
                "labels": labels,
            },
            payload=payload,
            citations=citations_for(output, subject_number=issue.number),
            input_context=context,
            input_sha256=issue.input_sha256(),
            proposal_base=proposal_base,
        )

    def review_pull_request(self, pull_request: PullRequest) -> GenerationResult:
        repository = self._require_repository(pull_request.repository_id)
        settings = self.settings_for(repository.id)
        self._require_enabled(settings, "pr_review")

        context = {
            "workflow": "pr_review",
            "repository": repository.full_name,
            "number": pull_request.number,
            "title": pull_request.title,
            "body": pull_request.body,
            "author": pull_request.author_login,
            "base_ref": pull_request.base_ref,
            "head_ref": pull_request.head_ref,
            "files": [
                {
                    "path": item.path,
                    "additions": item.additions,
                    "deletions": item.deletions,
                    "patch_summary": item.patch_summary,
                }
                for item in pull_request.files
            ],
        }
        try:
            output, proposal_base = self._generate(
                repository=repository,
                settings=settings,
                workflow="pr_review",
                subject_type="pull_request",
                subject_number=pull_request.number,
                context=context,
                candidate_numbers=(),
                input_sha256=pull_request.input_sha256(),
            )
        except _ExistingProposal as existing:
            return GenerationResult(proposal=existing.proposal, created=False)
        payload = output_as_payload(output)
        return self._store_proposal(
            repository=repository,
            workflow="pr_review",
            subject_type="pull_request",
            subject_number=pull_request.number,
            action_type="comment" if payload.get("proposed_comment") else "none",
            action_payload={"comment": payload.get("proposed_comment"), "labels": []},
            payload=payload,
            citations=citations_for(output, subject_number=pull_request.number),
            input_context=context,
            input_sha256=pull_request.input_sha256(),
            proposal_base=proposal_base,
        )

    def draft_release_notes(self, pull_requests: Sequence[PullRequest]) -> GenerationResult:
        if not pull_requests:
            raise WorkflowError("release notes require at least one merged pull request")
        repository = self._require_repository(pull_requests[0].repository_id)
        settings = self.settings_for(repository.id)
        self._require_enabled(settings, "release_notes")

        context = {
            "workflow": "release_notes",
            "repository": repository.full_name,
            "pull_requests": [
                {"number": item.number, "title": item.title, "author": item.author_login}
                for item in pull_requests
            ],
        }
        digest = hash_canonical(
            {
                "repository": repository.id,
                "numbers": [item.number for item in pull_requests],
            }
        )
        try:
            output, proposal_base = self._generate(
                repository=repository,
                settings=settings,
                workflow="release_notes",
                subject_type="repository",
                subject_number=None,
                context=context,
                candidate_numbers=(),
                input_sha256=digest,
            )
        except _ExistingProposal as existing:
            return GenerationResult(proposal=existing.proposal, created=False)
        payload = output_as_payload(output)
        citations = tuple(
            Citation(kind="pull_request", number=item.number, label=f"#{item.number}")
            for item in pull_requests
        )
        return self._store_proposal(
            repository=repository,
            workflow="release_notes",
            subject_type="repository",
            subject_number=None,
            action_type="none",
            action_payload={"comment": None, "labels": []},
            payload=payload,
            citations=citations,
            input_context=context,
            input_sha256=digest,
            proposal_base=proposal_base,
        )

    def _generate(
        self,
        *,
        repository: Repository,
        settings: RepositorySettings,
        workflow: WorkflowName,
        subject_type: SubjectType,
        subject_number: int | None,
        context: dict[str, Any],
        candidate_numbers: tuple[int, ...],
        input_sha256: str,
    ) -> tuple[Any, dict[str, Any]]:
        idempotency_key = hash_canonical(
            {
                "repository": repository.id,
                "workflow": workflow,
                "subject_type": subject_type,
                "subject_number": subject_number,
                "input_sha256": input_sha256,
            }
        )
        existing = self.store.find_proposal_by_idempotency(idempotency_key)
        if existing is not None:
            raise _ExistingProposal(existing)

        redacted_context = redact_value(context)
        assert isinstance(redacted_context, dict)
        bundle = build_prompt(workflow, redacted_context)
        request = ModelRequest(
            workflow=workflow,
            prompt_version=bundle.version,
            system_prompt=bundle.system_prompt,
            user_prompt=bundle.user_prompt,
            json_schema={},
        )
        try:
            response = self.gateway.complete(
                request, repository_id=repository.id, settings=settings
            )
        except BudgetExceeded as exc:
            self.store.append_audit(
                event_type="workflow.budget_blocked",
                repository_id=repository.id,
                subject_type=subject_type,
                subject_id=str(subject_number) if subject_number is not None else None,
                detail={"workflow": workflow, "reason": str(exc)[:200]},
            )
            raise
        except ProviderError as exc:
            self.store.append_audit(
                event_type="workflow.provider_error",
                repository_id=repository.id,
                subject_type=subject_type,
                subject_id=str(subject_number) if subject_number is not None else None,
                detail={"workflow": workflow, "error_class": exc.error_class},
            )
            raise WorkflowError(f"provider failed: {exc.error_class}") from exc

        try:
            output = parse_workflow_output(
                workflow,
                response.text,
                label_taxonomy=settings.label_taxonomy,
                candidate_numbers=candidate_numbers,
            )
        except ValidationError as exc:
            self.store.append_audit(
                event_type="workflow.schema_invalid",
                repository_id=repository.id,
                subject_type=subject_type,
                subject_id=str(subject_number) if subject_number is not None else None,
                detail={"workflow": workflow, "error": str(exc)[:300]},
            )
            raise WorkflowError(f"model output failed validation: {exc}") from exc

        return output, {
            "prompt_version": bundle.version,
            "provider": response.provider,
            "model": response.model,
            "request_id": response.request_id,
            "cost_usd": response.estimated_cost_usd,
            "idempotency_key": idempotency_key,
            "injection_signals": list(
                injection_signals(f"{context.get('title', '')}\n{context.get('body', '')}")
            ),
        }

    def _store_proposal(
        self,
        *,
        repository: Repository,
        workflow: WorkflowName,
        subject_type: SubjectType,
        subject_number: int | None,
        action_type: ActionType,
        action_payload: dict[str, Any],
        payload: dict[str, Any],
        citations: Sequence[Any],
        input_context: dict[str, Any],
        input_sha256: str,
        proposal_base: dict[str, Any],
    ) -> GenerationResult:
        redacted_payload = redact_value(payload)
        assert isinstance(redacted_payload, dict)
        stored_context = redact_value(input_context)
        assert isinstance(stored_context, dict)
        proposal_id = new_id("prop", repository.id, workflow, str(subject_number), input_sha256)
        created_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        proposal = Proposal(
            id=proposal_id,
            repository_id=repository.id,
            workflow=workflow,
            subject_type=subject_type,
            subject_number=subject_number,
            state="pending_approval",
            action_type=action_type,
            action_payload=action_payload,
            payload=redacted_payload,
            citations=tuple(citations),
            input_sha256=input_sha256,
            input_context=stored_context,
            prompt_version=proposal_base.get("prompt_version"),
            provider=proposal_base.get("provider"),
            model=proposal_base.get("model"),
            model_request_id=proposal_base.get("request_id"),
            confidence=redacted_payload.get("confidence"),
            risk=redacted_payload.get("risk"),
            cost_usd=float(proposal_base.get("cost_usd") or 0.0),
            created_by=self.created_by,
            created_at=created_at,
            expires_at=_iso_after(self.proposal_ttl_minutes),
            idempotency_key=proposal_base["idempotency_key"],
        )
        self.store.create_proposal(proposal)
        self.store.append_audit(
            event_type="proposal.created",
            repository_id=repository.id,
            actor_login=self.created_by,
            subject_type=subject_type,
            subject_id=proposal_id,
            detail={
                "workflow": workflow,
                "action_type": action_type,
                "confidence": proposal.confidence,
                "risk": proposal.risk,
                "injection_signals": proposal_base.get("injection_signals", []),
            },
        )
        return GenerationResult(proposal=proposal, created=True)

    # ------------------------------------------------------------- decisions
    def approve(self, proposal_id: str, actor: str) -> Proposal:
        proposal = self.store.approve_proposal(proposal_id, actor)
        self.store.append_audit(
            event_type="proposal.approved",
            repository_id=proposal.repository_id,
            actor_login=actor,
            subject_type=proposal.subject_type,
            subject_id=proposal.id,
            detail={"workflow": proposal.workflow, "action_type": proposal.action_type},
        )
        return proposal

    def reject(self, proposal_id: str, actor: str, reason: str = "") -> Proposal:
        proposal = self.store.reject_proposal(proposal_id, actor, reason)
        self.store.append_audit(
            event_type="proposal.rejected",
            repository_id=proposal.repository_id,
            actor_login=actor,
            subject_type=proposal.subject_type,
            subject_id=proposal.id,
            detail={"reason": redact(reason).text[:200]},
        )
        return proposal

    # ------------------------------------------------------------- execution
    def execute(self, proposal_id: str, actor: str) -> Proposal:
        """Execute an approved proposal exactly once.

        The claim happens before any forge call. If the forge call fails, the
        proposal lands in `action_failed` and can be retried explicitly, never
        automatically.
        """

        proposal = self.store.claim_for_execution(proposal_id, actor)
        repository = self._require_repository(proposal.repository_id)
        try:
            if proposal.action_type == "comment":
                body = str(proposal.action_payload.get("comment") or "").strip()
                if not body:
                    raise WorkflowError("approved comment proposal has no comment body")
                posted = self.adapter.post_comment(
                    repository, int(proposal.subject_number or 0), body
                )
                self.store.save_comment(
                    comment_id=posted.external_id or new_id("cmt", proposal.id),
                    repository_id=proposal.repository_id,
                    issue_number=int(proposal.subject_number or 0),
                    body=redact(posted.body).text,
                    author_login=self.created_by,
                    source="posted",
                    idempotency_key=proposal.id,
                )
            elif proposal.action_type == "label":
                labels = [
                    str(label) for label in proposal.action_payload.get("labels", []) if label
                ]
                if not labels:
                    raise WorkflowError("approved label proposal has no labels")
                self.adapter.add_labels(repository, int(proposal.subject_number or 0), labels)
            elif proposal.action_type == "none":
                pass
            else:
                raise WorkflowError(f"unsupported action type: {proposal.action_type}")
        except (
            AdapterAuthError,
            AdapterNotFound,
            AdapterRateLimited,
            AdapterTransient,
            AdapterError,
        ) as exc:
            error_class = (
                "auth"
                if isinstance(exc, AdapterAuthError)
                else "not_found"
                if isinstance(exc, AdapterNotFound)
                else "rate_limited"
                if isinstance(exc, AdapterRateLimited)
                else "transport"
                if isinstance(exc, AdapterTransient)
                else "invalid_request"
            )
            failed = self.store.finish_execution(
                proposal.id, success=False, error=str(exc), error_class=error_class
            )
            self.store.append_audit(
                event_type="action.failed",
                repository_id=proposal.repository_id,
                actor_login=actor,
                subject_type=proposal.subject_type,
                subject_id=proposal.id,
                detail={"error_class": error_class, "attempt": failed.attempt_count},
            )
            return failed
        except (WorkflowError, Unauthorized, Conflict) as exc:
            failed = self.store.finish_execution(
                proposal.id, success=False, error=str(exc), error_class="invalid_request"
            )
            self.store.append_audit(
                event_type="action.failed",
                repository_id=proposal.repository_id,
                actor_login=actor,
                subject_type=proposal.subject_type,
                subject_id=proposal.id,
                detail={"error_class": "invalid_request"},
            )
            return failed

        executed = self.store.finish_execution(proposal.id, success=True)
        self.store.append_audit(
            event_type="action.executed",
            repository_id=proposal.repository_id,
            actor_login=actor,
            subject_type=proposal.subject_type,
            subject_id=proposal.id,
            detail={
                "workflow": proposal.workflow,
                "action_type": proposal.action_type,
                "attempt": executed.attempt_count,
            },
        )
        return executed

    def expire(self) -> int:
        return self.store.expire_due_proposals()

    # --------------------------------------------------------------- helpers
    def _require_repository(self, repository_id: str) -> Repository:
        repository = self.store.get_repository(repository_id)
        if repository is None:
            raise NotFound(f"repository {repository_id} not registered")
        return repository

    @staticmethod
    def _require_enabled(settings: RepositorySettings, workflow: WorkflowName) -> None:
        if not settings.workflow_enabled(workflow):
            raise WorkflowDisabled(f"workflow {workflow} is disabled for this repository")


def proposal_summary(proposal: Proposal) -> dict[str, Any]:
    payload = proposal.payload
    comment = proposal.action_payload.get("comment")
    return {
        "id": proposal.id,
        "repository_id": proposal.repository_id,
        "workflow": proposal.workflow,
        "subject_type": proposal.subject_type,
        "subject_number": proposal.subject_number,
        "state": proposal.state,
        "action_type": proposal.action_type,
        "confidence": proposal.confidence,
        "risk": proposal.risk,
        "summary": payload.get("summary", ""),
        "comment_preview": (comment[:280] + "…")
        if isinstance(comment, str) and len(comment) > 280
        else comment,
        "labels": proposal.action_payload.get("labels", []),
        "citations": [citation.as_dict() for citation in proposal.citations],
        "created_at": proposal.created_at,
        "expires_at": proposal.expires_at,
        "approved_by": proposal.approved_by,
        "executed_at": proposal.executed_at,
        "attempt_count": proposal.attempt_count,
        "last_error": proposal.last_error,
        "cost_usd": proposal.cost_usd,
        "input_sha256": proposal.input_sha256,
    }


def proposal_detail(proposal: Proposal) -> dict[str, Any]:
    detail = proposal.as_dict()
    detail["input_context"] = proposal.input_context
    detail["payload_canonical"] = canonical_json(proposal.payload)
    return detail


__all__ = [
    "Expired",
    "GenerationResult",
    "WorkflowDisabled",
    "WorkflowEngine",
    "WorkflowError",
    "proposal_detail",
    "proposal_summary",
]
