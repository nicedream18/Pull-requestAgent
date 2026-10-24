import requests

from prguard.github import GitHubClient, GitHubRequestError


class FakeResponse:
    def __init__(self, status_code, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, headers, timeout, allow_redirects):
        self.calls.append((url, headers, timeout, allow_redirects))
        return self.response


def test_github_client_uses_read_only_fixed_api_origin():
    session = FakeSession(FakeResponse(200, {"tree": [], "truncated": False}))
    client = GitHubClient(token="secret", session=session)
    assert client.fetch_tree("acme/widgets", "abc") == []
    url, headers, timeout, allow_redirects = session.calls[0]
    assert url.startswith("https://api.github.com/repos/acme/widgets/git/trees/abc")
    assert headers["Authorization"] == "Bearer secret"
    assert timeout == (3.05, 20)
    assert allow_redirects is False


def test_github_client_classifies_auth_failure():
    session = FakeSession(FakeResponse(401, {}))
    try:
        GitHubClient(session=session).fetch_tree("acme/widgets", "abc")
    except GitHubRequestError as exc:
        assert exc.status_code == 401
        assert "authentication" in str(exc).lower()
    else:
        raise AssertionError("expected GitHubRequestError")


def test_history_lookup_is_opt_in_without_a_token():
    session = FakeSession(FakeResponse(200, []))
    client = GitHubClient(token="", session=session)
    assert client.find_related_prs("acme/widgets", ["app.py"], 42) == []
    assert session.calls == []
