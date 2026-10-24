import os
import sys
from typing import List, Optional, Type

import requests
from urllib.parse import urlsplit
from crewai.tools import BaseTool
from github import Auth, Github
from pydantic import BaseModel, Field

class FetchPRFilesInput(BaseModel):
    """Input schema for FetchPRFilesTool."""
    pr_patch_url: str = Field(..., description="URL of the PR patch file to analyze")

class FetchPRFilesTool(BaseTool):
    name: str = "Fetch PR Modified Files"
    description: str = "Retrieves the patch and contents of all modified files in a GitHub pull request"
    args_schema: Type[BaseModel] = FetchPRFilesInput

    def _run(self, pr_patch_url: str) -> str:
        parsed = urlsplit(pr_patch_url)
        parts = parsed.path.strip("/").split("/")
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or len(parts) != 4
            or parts[2] != "pull"
            or not parts[3].removesuffix(".patch").isdigit()
        ):
            return "Error fetching PR patch: URL must be a canonical GitHub pull request patch URL"
        try:
            response = requests.get(
                pr_patch_url,
                timeout=(3.05, 20),
                allow_redirects=False,
                headers={"Accept": "text/plain", "User-Agent": "PRGuard/0.1"},
            )
        except requests.Timeout:
            return "Error fetching PR patch: request timed out"
        except requests.RequestException as exc:
            return f"Error fetching PR patch: {type(exc).__name__}"

        if response.status_code != 200:
            return f"Error fetching PR patch: {response.status_code}"
        if len(response.content) > 500_000:
            return "Error fetching PR patch: response exceeds the 500000 character safety limit"
        return response.text

class RelatedPRsInput(BaseModel):
    """Input schema for FindRelatedPRsTool."""
    repo_name: str = Field(..., description="Full repository name (owner/repo)")
    file_paths: List[str] = Field(..., description="List of file paths to find related PRs for")
    pr_patch_url: Optional[str] = Field(
        default=None,
        description="Patch URL of the pull request under review, so it can be excluded from the results",
    )


# Walking the commit history of the changed files is far more reliable than scanning the
# most recent closed PRs: active repos close dozens of PRs a day, so a fixed window of 20
# PRs often never touches the file being reviewed.
DEFAULT_SCAN_COMMITS = 40
MAX_RELATED_PRS = 5


def _normalize_path(path: str) -> str:
    """Turn `a/src/x.py` / `./src/x.py` patch paths into repo-relative paths."""
    path = path.strip().split("\t")[0]
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path.lstrip("./")


class FindRelatedPRsTool(BaseTool):
    name: str = "Find Related Pull Requests"
    description: str = (
        "Finds pull requests that previously modified the given files, including PR number, "
        "title, URL and merge date"
    )
    args_schema: Type[BaseModel] = RelatedPRsInput

    def _run(
        self,
        repo_name: str,
        file_paths: List[str],
        pr_patch_url: Optional[str] = None,
    ) -> str:
        token = os.getenv('GITHUB_TOKEN_CODEREVIEW')

        if not token:
            return "GitHub token not provided in environment or parameters. Proceed without searching for related PRs."

        paths = [p for p in (_normalize_path(p) for p in file_paths) if p]
        if not paths:
            return "No file paths provided, cannot search for related pull requests."

        # e.g. https://github.com/owner/repo/pull/123.patch -> 123
        current_pr_number = None
        if pr_patch_url:
            parts = pr_patch_url.rstrip("/").removesuffix(".patch").split("/")
            if len(parts) >= 2 and parts[-2] == "pull" and parts[-1].isdigit():
                current_pr_number = int(parts[-1])

        scan_limit = int(os.getenv("RELATED_PR_SCAN_COMMITS", DEFAULT_SCAN_COMMITS))

        try:
            repo = Github(auth=Auth.Token(token)).get_repo(repo_name)
        except Exception as e:
            return (
                f"Could not access repository '{repo_name}': {e}. "
                "Check that GITHUB_TOKEN_CODEREVIEW is valid and has read access to this repo."
            )

        related: dict[int, dict] = {}
        scanned = 0
        errors = 0

        for path in paths:
            try:
                commits = repo.get_commits(path=path)
            except Exception:
                errors += 1
                continue

            for commit in commits[:scan_limit]:
                scanned += 1
                try:
                    for pr in commit.get_pulls():
                        if pr.number == current_pr_number or pr.number in related:
                            continue
                        related[pr.number] = {
                            "number": pr.number,
                            "title": pr.title,
                            "url": pr.html_url,
                            "date": commit.commit.committer.date.strftime("%Y-%m-%d"),
                        }
                except Exception:
                    errors += 1

                if len(related) >= MAX_RELATED_PRS:
                    break
            if len(related) >= MAX_RELATED_PRS:
                break

        if not related:
            return (
                f"No related pull requests found for {', '.join(paths)} "
                f"(scanned {scanned} commits)."
            )

        lines = [
            f"Found {len(related)} related pull request(s) that previously modified the same files:"
        ]
        for pr in sorted(related.values(), key=lambda x: x["date"], reverse=True):
            lines.append(
                f'- #{pr["number"]} "{pr["title"]}" (last change {pr["date"]}) {pr["url"]}'
            )
        if errors:
            lines.append(f"(Note: {errors} commit lookup(s) failed and were skipped.)")
        return "\n".join(lines)


class RelatedFilesInput(BaseModel):
    """Input schema for FindRelatedFilesTool."""
    repo_name: str = Field(..., description="Full repository name (owner/repo)")
    file_paths: List[str] = Field(..., description="List of modified file paths")


MAX_RELATED_FILES = 12
MAX_IMPORT_LOOKUPS = 8
MAX_SIBLINGS_PER_FILE = 6


class FindRelatedFilesTool(BaseTool):
    name: str = "Find Related Files"
    description: str = (
        "Identifies files related to the modified files: siblings in the same directory and "
        "modules imported by the modified files"
    )
    args_schema: Type[BaseModel] = RelatedFilesInput

    def _run(self, repo_name: str, file_paths: List[str]) -> str:
        token = os.getenv('GITHUB_TOKEN_CODEREVIEW')

        if not token:
            return "GitHub token not provided in environment or parameters. Proceed without searching for related files."

        paths = [p for p in (_normalize_path(p) for p in file_paths) if p]
        if not paths:
            return "No file paths provided, cannot search for related files."

        try:
            repo = Github(auth=Auth.Token(token)).get_repo(repo_name)
        except Exception as e:
            return (
                f"Could not access repository '{repo_name}': {e}. "
                "Check that GITHUB_TOKEN_CODEREVIEW is valid and has read access to this repo."
            )

        changed = set(paths)
        relations: dict[str, str] = {}
        missing: List[str] = []

        for file_path in paths:
            try:
                content = repo.get_contents(file_path)
                source = content.decoded_content.decode("utf-8", errors="replace")
            except Exception:
                source = ""
                missing.append(file_path)

            for group in self._imported_modules(source, file_path)[:MAX_IMPORT_LOOKUPS]:
                for candidate in group:
                    try:
                        repo.get_contents(candidate)
                    except Exception:
                        continue
                    if candidate not in changed and candidate not in relations:
                        relations[candidate] = f"imported by {file_path}"
                    break

            for sibling in self._siblings(repo, file_path)[:MAX_SIBLINGS_PER_FILE]:
                if sibling not in changed:
                    relations.setdefault(sibling, f"same directory as {file_path}")

        if not relations:
            detail = (
                f" These paths no longer exist at HEAD (moved or deleted): {', '.join(missing)}."
                if missing
                else " No sibling files or resolvable imports were found."
            )
            return f"No related files identified for {', '.join(paths)}.{detail}"

        shown = list(relations.items())[:MAX_RELATED_FILES]
        header = f"Found {len(relations)} related file(s)"
        if len(relations) > len(shown):
            header += f", showing first {len(shown)}"
        lines = [header + ":"]
        for path, reason in shown:
            lines.append(f"- {path} ({reason})")
        return "\n".join(lines)

    def _siblings(self, repo, file_path: str) -> List[str]:
        directory = file_path.rsplit("/", 1)[0] if "/" in file_path else ""
        try:
            entries = repo.get_contents(directory) if directory else repo.get_contents("")
        except Exception:
            return []

        return [
            e.path
            for e in entries
            if e.type == "file" and e.name.endswith(".py") and e.path != file_path
        ] or [
            e.path for e in entries if e.type == "file" and e.path != file_path
        ]

    def _imported_modules(self, source: str, file_path: str) -> List[List[str]]:
        """Group candidate repo paths per internal import, best guess first."""
        if not file_path.endswith(".py") or not source:
            return []

        path_parts = file_path.split("/")[:-1]
        package = ".".join(path_parts)
        groups: List[List[str]] = []
        seen_modules = set()

        for line in source.splitlines():
            line = line.strip()
            if line.startswith("from ") and " import " in line:
                module = line[5:].split(" import ")[0].strip()
                level = len(module) - len(module.lstrip("."))
                if level:
                    base = package.split(".")
                    base = base[: len(base) - level + 1] if level > 1 else base
                    module = ".".join([p for p in base if p] + [module.lstrip(".")])
            elif line.startswith("import "):
                module = line[7:].split(",")[0].split(" as ")[0].strip()
            else:
                continue

            if not module or module in seen_modules:
                continue
            seen_modules.add(module)

            top_level = module.split(".")[0]
            if top_level in sys.stdlib_module_names:
                continue

            # Only follow imports that live inside this repository. Third-party packages
            # (pydantic, opentelemetry, ...) would otherwise consume every lookup slot.
            if top_level not in path_parts:
                continue

            # Repos often nest packages (e.g. lib/crewai/src/crewai/crew.py importing
            # "crewai.agent"), so try every matching path segment, deepest first.
            group: List[str] = []
            for index in reversed([i for i, part in enumerate(path_parts) if part == top_level]):
                base = "/".join(path_parts[:index] + module.split("."))
                for candidate in (base + ".py", base + "/__init__.py"):
                    if candidate not in group:
                        group.append(candidate)

            if group:
                groups.append(group)

        return groups


class CodeReviewInput(BaseModel):
    """Input schema for GenerateReviewTool."""
    analysis_results: str = Field(..., description="Results from code analysis")
    related_prs: str = Field(..., description="Information about related PRs")
    related_files: str = Field(..., description="Information about related files")

class GenerateReviewTool(BaseTool):
    name: str = "Generate Code Review"
    description: str = "Generates a comprehensive code review comment based on all analyses"
    args_schema: Type[BaseModel] = CodeReviewInput

    def _run(self, analysis_results: str, related_prs: str, related_files: str) -> str:
        # Combine all inputs into a well-structured markdown review
        review = f"""
# Code Review Summary

## Code Analysis
{analysis_results}

## Historical Context
{related_prs}

## Related Files Impact
{related_files}

## Recommendations
- [Add prioritized recommendations based on the analysis]
"""
        return review
