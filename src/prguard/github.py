from __future__ import annotations

import base64
import os
import re
from typing import Any
from urllib.parse import quote

import requests

from prguard.models import PRFile, PRReviewRequest, PRSnapshot

GITHUB_API = "https://api.github.com"
MAX_FILE_PAGES = 20
MAX_FILES = 1000
MAX_DIFF_CHARS = 500_000
MAX_CONTENT_BYTES = 1_000_000
MAX_HISTORY_PATHS = 3
MAX_HISTORY_COMMITS_PER_PATH = 10
MAX_HISTORY_PRS = 5


class GitHubRequestError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class GitHubClient:
    """Read-only GitHub REST client with fixed-origin requests and bounded responses."""

    def __init__(self, token: str | None = None, session: requests.Session | None = None):
        self.token = token if token is not None else os.getenv("GITHUB_TOKEN_CODEREVIEW")
        self.session = session or requests.Session()

    def _request(self, url: str, *, accept: str = "application/vnd.github+json") -> Any:
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PRGuard/0.1",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self.session.get(url, headers=headers, timeout=(3.05, 20), allow_redirects=False)
        except requests.Timeout as exc:
            raise GitHubRequestError(408, "GitHub request timed out") from exc
        except requests.RequestException as exc:
            raise GitHubRequestError(502, f"GitHub request failed: {type(exc).__name__}") from exc

        if response.status_code >= 300:
            messages = {
                401: "GitHub authentication failed (401)",
                403: "GitHub access forbidden or rate limited (403)",
                404: "GitHub repository or pull request not found (404)",
                429: "GitHub rate limit exceeded (429)",
            }
            raise GitHubRequestError(response.status_code, messages.get(response.status_code, f"GitHub returned HTTP {response.status_code}"))
        return response

    def fetch_pr(self, request: PRReviewRequest) -> PRSnapshot:
        owner, repo = request.repo.split("/", 1)
        repo_path = f"repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
        pr_url = f"{GITHUB_API}/{repo_path}/pulls/{request.pr_number}"
        metadata = self._request(pr_url).json()
        files = self._fetch_files(f"{pr_url}/files")

        parts: list[str] = []
        omitted: list[str] = []
        used_chars = 0
        for item in files:
            if not item.patch:
                continue
            rendered = f"diff -- {item.path} ({item.status})\n{item.patch}\n"
            if used_chars + len(rendered) > MAX_DIFF_CHARS:
                omitted.append(item.path)
                continue
            parts.append(rendered)
            used_chars += len(rendered)

        return PRSnapshot(
            repo=request.repo,
            pr_number=request.pr_number,
            title=str(metadata.get("title", ""))[:500],
            body=str(metadata.get("body") or "")[:20_000],
            base_sha=metadata["base"]["sha"],
            head_sha=metadata["head"]["sha"],
            files=files,
            diff="\n".join(parts),
            diff_complete=not omitted,
            diff_omitted_files=omitted,
        )

    def _fetch_files(self, first_url: str) -> list[PRFile]:
        files: list[PRFile] = []
        url: str | None = first_url
        page_count = 0
        while url and page_count < MAX_FILE_PAGES and len(files) < MAX_FILES:
            response = self._request(url)
            data = response.json()
            if not isinstance(data, list):
                raise GitHubRequestError(502, "GitHub returned an invalid pull request files response")
            for item in data:
                files.append(PRFile(
                    path=item["filename"],
                    status=item.get("status", "modified"),
                    patch=item.get("patch"),
                    additions=int(item.get("additions", 0)),
                    deletions=int(item.get("deletions", 0)),
                    changes=int(item.get("changes", 0)),
                    previous_filename=item.get("previous_filename"),
                ))
                if len(files) >= MAX_FILES:
                    break
            url = self._next_link(response.headers.get("Link", ""))
            page_count += 1
        return files

    @staticmethod
    def _next_link(link_header: str) -> str | None:
        for item in link_header.split(","):
            match = re.match(r'\s*<([^>]+)>\s*;\s*rel="next"', item)
            if match:
                url = match.group(1)
                if url.startswith(f"{GITHUB_API}/"):
                    return url
        return None

    def fetch_tree(self, repo: str, ref: str) -> list[str]:
        owner, name = repo.split("/", 1)
        url = f"{GITHUB_API}/repos/{quote(owner, safe='')}/{quote(name, safe='')}/git/trees/{quote(ref, safe='')}?recursive=1"
        data = self._request(url).json()
        if data.get("truncated"):
            return [entry["path"] for entry in data.get("tree", []) if entry.get("type") == "blob"]
        return [entry["path"] for entry in data.get("tree", []) if entry.get("type") == "blob"]

    def fetch_file(self, repo: str, path: str, ref: str) -> str | None:
        if path.startswith("/") or ".." in path.split("/"):
            return None
        owner, name = repo.split("/", 1)
        encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
        url = f"{GITHUB_API}/repos/{quote(owner, safe='')}/{quote(name, safe='')}/contents/{encoded_path}?ref={quote(ref, safe='')}"
        try:
            data = self._request(url).json()
        except GitHubRequestError as exc:
            if exc.status_code == 404:
                return None
            raise
        if data.get("encoding") != "base64" or not data.get("content"):
            return None
        try:
            content = base64.b64decode(data["content"], validate=False)
        except (ValueError, TypeError):
            return None
        if len(content) > MAX_CONTENT_BYTES:
            return None
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def find_related_prs(self, repo: str, paths: list[str], current_pr_number: int) -> list[dict[str, str | int]]:
        """Map recent file commits to earlier PRs with bounded REST requests."""
        if not self.token:
            return []
        owner, name = repo.split("/", 1)
        base = f"{GITHUB_API}/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        related: dict[int, dict[str, str | int]] = {}
        for path in paths[:MAX_HISTORY_PATHS]:
            commits_url = f"{base}/commits?path={quote(path, safe='')}&per_page={MAX_HISTORY_COMMITS_PER_PATH}"
            commits = self._request(commits_url).json()
            if not isinstance(commits, list):
                continue
            for commit in commits[:MAX_HISTORY_COMMITS_PER_PATH]:
                sha = commit.get("sha")
                if not sha:
                    continue
                pulls_url = f"{base}/commits/{quote(str(sha), safe='')}/pulls"
                try:
                    pulls = self._request(pulls_url, accept="application/vnd.github+json").json()
                except GitHubRequestError:
                    continue
                if not isinstance(pulls, list):
                    continue
                for pull in pulls:
                    number = pull.get("number")
                    if not isinstance(number, int) or number == current_pr_number:
                        continue
                    related.setdefault(number, {
                        "number": number,
                        "title": str(pull.get("title") or "")[:300],
                        "url": str(pull.get("html_url") or ""),
                        "state": str(pull.get("state") or ""),
                    })
                    if len(related) >= MAX_HISTORY_PRS:
                        return list(related.values())
        return list(related.values())
