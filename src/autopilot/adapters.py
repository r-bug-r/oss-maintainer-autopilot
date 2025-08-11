"""Repository adapters: the boundary between Autopilot and a forge.

GitHub is implemented. GitLab is a declared, unimplemented boundary: calling it
raises rather than silently degrading, because a half-working adapter would be
a security and correctness risk.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .models import Comment, Issue, PullRequest, PullRequestFile, Repository
from .store import Store


class AdapterAuthError(AdapterError):
    pass


class AdapterNotFound(AdapterError):
    pass


class AdapterRateLimited(AdapterError):
    def __init__(self, message: str = "forge rate limit reached") -> None:
        super().__init__(message, retryable=True, status=429)


class AdapterTransient(AdapterError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=True)


class UnsupportedProvider(AdapterError):
    pass


@dataclass(frozen=True, slots=True)
class PostedComment:
    external_id: str
    body: str


class RepositoryAdapter(Protocol):
    """Read and write operations a workflow needs from a forge."""

    def get_issue(self, repository: Repository, number: int) -> Issue: ...

    def list_issues(self, repository: Repository, *, limit: int = 50) -> list[Issue]: ...

    def get_pull_request(self, repository: Repository, number: int) -> PullRequest: ...

    def list_issue_comments(self, repository: Repository, number: int) -> list[Comment]: ...

    def post_comment(self, repository: Repository, number: int, body: str) -> PostedComment: ...

    def add_labels(self, repository: Repository, number: int, labels: Sequence[str]) -> None: ...


class GitHubAdapter:
    """Minimal GitHub REST client using the standard library.

    Only the endpoints required by the implemented workflows are reachable, and
    the base URL is fixed at construction so a redirect cannot send the token
    to another host.
    """

    def __init__(
        self,
        *,
        token: str,
        base_url: str = "https://api.github.com",
        timeout_seconds: float = 15.0,
        user_agent: str = "oss-maintainer-autopilot",
    ) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AdapterError("GitHub base URL must be an absolute http(s) URL")
        if not token:
            raise AdapterAuthError("a GitHub token is required for the GitHub adapter")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds
        self._user_agent = user_agent

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
        accept: str = "application/vnd.github+json",
    ) -> tuple[int, dict[str, str], bytes]:
        url = f"{self._base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(  # noqa: S310 - scheme validated in __init__
            url,
            data=data,
            method=method,
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {self._token}",
                "User-Agent": self._user_agent,
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            opener = urllib.request.build_opener()
            with opener.open(request, timeout=self._timeout) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            if exc.code in {401, 403}:
                if exc.code == 403 and "rate limit" in payload.decode("utf-8", "replace").lower():
                    raise AdapterRateLimited() from exc
                raise AdapterAuthError(
                    f"GitHub rejected the request ({exc.code})", status=exc.code
                ) from exc
            if exc.code == 404:
                raise AdapterNotFound("GitHub resource not found", status=404) from exc
            if exc.code == 429:
                raise AdapterRateLimited() from exc
            if exc.code >= 500:
                raise AdapterTransient(f"GitHub server error ({exc.code})") from exc
            raise AdapterError(f"GitHub request failed ({exc.code})", status=exc.code) from exc
        except TimeoutError as exc:
            raise AdapterTransient("GitHub request timed out") from exc
        except urllib.error.URLError as exc:
            raise AdapterTransient("GitHub transport failure") from exc

    @staticmethod
    def _json(payload: bytes) -> object:
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdapterError("GitHub returned an unparsable body") from exc

    def _repo_path(self, repository: Repository) -> str:
        if repository.provider != "github":
            raise UnsupportedProvider("GitHubAdapter only handles github repositories")
        return f"/repos/{urllib.parse.quote(repository.full_name, safe='/')}"

    def get_issue(self, repository: Repository, number: int) -> Issue:
        status, _, payload = self._request("GET", f"{self._repo_path(repository)}/issues/{number}")
        if status != 200:
            raise AdapterError(f"unexpected status {status}")
        return self._issue_from_json(self._json(payload), repository)

    def _issue_from_json(self, raw: object, repository: Repository) -> Issue:
        if not isinstance(raw, dict):
            raise AdapterError("issue payload must be an object")
        user = _obj(raw.get("user"))
        raw_labels = raw.get("labels")
        labels = tuple(
            name
            for item in (raw_labels if isinstance(raw_labels, list) else [])
            if isinstance(item, dict) and isinstance(name := item.get("name"), str)
        )
        return Issue(
            repository_id=repository.id,
            number=_int(raw.get("number")),
            title=_str(raw.get("title")),
            body=_str(raw.get("body")),
            author_login=_opt_str(user.get("login")),
            state=_str(raw.get("state"), "open"),
            labels=labels,
            url=_opt_str(raw.get("html_url")),
            created_at=_opt_str(raw.get("created_at")),
            updated_at=_opt_str(raw.get("updated_at")),
            external_id=_opt_str(str(raw["id"])) if raw.get("id") is not None else None,
        )

    def list_issues(self, repository: Repository, *, limit: int = 50) -> list[Issue]:
        query = urllib.parse.urlencode({"state": "open", "per_page": min(limit, 100)})
        status, _, payload = self._request("GET", f"{self._repo_path(repository)}/issues?{query}")
        if status != 200:
            raise AdapterError(f"unexpected status {status}")
        raw = self._json(payload)
        if not isinstance(raw, list):
            raise AdapterError("issue list must be an array")
        issues = []
        for item in raw:
            if isinstance(item, dict) and "pull_request" in item:
                continue
            issues.append(self._issue_from_json(item, repository))
        return issues[:limit]

    def get_pull_request(self, repository: Repository, number: int) -> PullRequest:
        base = self._repo_path(repository)
        status, _, payload = self._request("GET", f"{base}/pulls/{number}")
        if status != 200:
            raise AdapterError(f"unexpected status {status}")
        raw = self._json(payload)
        if not isinstance(raw, dict):
            raise AdapterError("pull request payload must be an object")
        files: list[PullRequestFile] = []
        status, _, files_payload = self._request("GET", f"{base}/pulls/{number}/files")
        if status == 200:
            raw_files = self._json(files_payload)
            if isinstance(raw_files, list):
                for item in raw_files:
                    if isinstance(item, dict) and isinstance(filename := item.get("filename"), str):
                        files.append(
                            PullRequestFile(
                                path=filename,
                                additions=_int(item.get("additions")),
                                deletions=_int(item.get("deletions")),
                                patch_summary=_str(item.get("patch"))[:400],
                            )
                        )
        user = _obj(raw.get("user"))
        base_ref = _obj(raw.get("base"))
        head_ref = _obj(raw.get("head"))
        return PullRequest(
            repository_id=repository.id,
            number=_int(raw.get("number")),
            title=_str(raw.get("title")),
            body=_str(raw.get("body")),
            author_login=_opt_str(user.get("login")),
            state=_str(raw.get("state"), "open"),
            base_ref=_str(base_ref.get("ref")),
            head_ref=_str(head_ref.get("ref")),
            files=tuple(files),
            external_id=_opt_str(str(raw["id"])) if raw.get("id") is not None else None,
        )

    def list_issue_comments(self, repository: Repository, number: int) -> list[Comment]:
        path = f"{self._repo_path(repository)}/issues/{number}/comments?per_page=100"
        status, _, payload = self._request("GET", path)
        if status != 200:
            raise AdapterError(f"unexpected status {status}")
        raw = self._json(payload)
        if not isinstance(raw, list):
            raise AdapterError("comment list must be an array")
        comments = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            user = _obj(item.get("user"))
            comments.append(
                Comment(
                    id=str(item.get("id", "")),
                    repository_id=repository.id,
                    issue_number=number,
                    body=_str(item.get("body")),
                    author_login=_str(user.get("login")),
                    source="fetched",
                    created_at=_str(item.get("created_at")),
                )
            )
        return comments

    def post_comment(self, repository: Repository, number: int, body: str) -> PostedComment:
        status, _, payload = self._request(
            "POST",
            f"{self._repo_path(repository)}/issues/{number}/comments",
            body={"body": body},
        )
        if status not in {200, 201}:
            raise AdapterError(f"unexpected status {status}")
        raw = self._json(payload)
        if not isinstance(raw, dict):
            raise AdapterError("comment payload must be an object")
        return PostedComment(external_id=str(raw.get("id", "")), body=str(raw.get("body", body)))

    def add_labels(self, repository: Repository, number: int, labels: Sequence[str]) -> None:
        status, _, _ = self._request(
            "POST",
            f"{self._repo_path(repository)}/issues/{number}/labels",
            body={"labels": list(labels)},
        )
        if status not in {200, 201}:
            raise AdapterError(f"unexpected status {status}")


