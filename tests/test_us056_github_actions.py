"""US-056: GitHub actions acceptance tests.

Covers:
- issues retrieved
- commits triggered
- errors handled
- Typecheck passes
"""

import base64
from unittest.mock import MagicMock, patch

import pytest

from rex.github_service import GitHubService, Issue


class TestIssuesRetrieved:
    """issues retrieved — list_issues() returns Issue objects."""

    def test_list_issues_returns_issue_objects(self):
        """list_issues() returns a list of Issue dataclass instances."""
        mock_data = {
            "GET:/repos/alice/my-repo/issues": [
                {
                    "number": 1,
                    "title": "Fix the bug",
                    "state": "open",
                    "html_url": "https://github.com/alice/my-repo/issues/1",
                    "user": {"login": "alice"},
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-02T00:00:00Z",
                    "labels": [],
                }
            ]
        }
        service = GitHubService(mock_mode=True)
        service._mock_data = mock_data

        issues = service.list_issues("alice/my-repo")

        assert isinstance(issues, list)
        assert len(issues) == 1
        assert isinstance(issues[0], Issue)

    def test_list_issues_fields_populated(self):
        """Issue objects have all required fields populated."""
        mock_data = {
            "GET:/repos/alice/my-repo/issues": [
                {
                    "number": 42,
                    "title": "Critical bug",
                    "state": "open",
                    "html_url": "https://github.com/alice/my-repo/issues/42",
                    "user": {"login": "bob"},
                    "created_at": "2024-03-01T10:00:00Z",
                    "updated_at": "2024-03-02T12:00:00Z",
                    "labels": [{"name": "bug"}, {"name": "priority"}],
                }
            ]
        }
        service = GitHubService(mock_mode=True)
        service._mock_data = mock_data

        issues = service.list_issues("alice/my-repo")
        issue = issues[0]

        assert issue.number == 42
        assert issue.title == "Critical bug"
        assert issue.state == "open"
        assert issue.url == "https://github.com/alice/my-repo/issues/42"
        assert issue.author == "bob"
        assert issue.created_at == "2024-03-01T10:00:00Z"
        assert "bug" in issue.labels
        assert "priority" in issue.labels

    def test_list_issues_excludes_pull_requests(self):
        """list_issues() filters out pull request entries from the response."""
        mock_data = {
            "GET:/repos/alice/my-repo/issues": [
                {
                    "number": 1,
                    "title": "Real issue",
                    "state": "open",
                    "html_url": "https://github.com/alice/my-repo/issues/1",
                    "user": {"login": "alice"},
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "labels": [],
                },
                {
                    "number": 2,
                    "title": "A pull request",
                    "state": "open",
                    "html_url": "https://github.com/alice/my-repo/pull/2",
                    "user": {"login": "charlie"},
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "labels": [],
                    "pull_request": {"url": "..."},
                },
            ]
        }
        service = GitHubService(mock_mode=True)
        service._mock_data = mock_data

        issues = service.list_issues("alice/my-repo")

        assert len(issues) == 1
        assert issues[0].number == 1

    def test_list_issues_empty(self):
        """list_issues() returns empty list when no issues exist."""
        mock_data = {"GET:/repos/alice/my-repo/issues": []}
        service = GitHubService(mock_mode=True)
        service._mock_data = mock_data

        issues = service.list_issues("alice/my-repo")

        assert issues == []

    def test_list_issues_state_filter(self):
        """list_issues() passes state parameter to the API."""
        service = GitHubService()

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = []
        fake_response.raise_for_status.return_value = None

        with (
            patch("rex.github_service.get_credential_manager") as mock_cm,
            patch("requests.request", return_value=fake_response) as mock_req,
        ):
            mock_manager = MagicMock()
            mock_manager.get_token.return_value = "ghp_token"
            mock_cm.return_value = mock_manager

            service.list_issues("alice/my-repo", state="closed")

        call_params = mock_req.call_args[1]["params"]
        assert call_params["state"] == "closed"


class TestCommitsTriggered:
    """commits triggered — create_commit() sends a commit to the GitHub API."""

    def test_create_commit_calls_contents_api(self):
        """create_commit() sends PUT request to /repos/{repo}/contents/{path}."""
        service = GitHubService()

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "commit": {
                "sha": "abc123def456",  # pragma: allowlist secret -- commit identifier fixture
                "html_url": "https://github.com/alice/my-repo/commit/abc123def456",
            }
        }
        fake_response.raise_for_status.return_value = None

        with (
            patch("rex.github_service.get_credential_manager") as mock_cm,
            patch("requests.request", return_value=fake_response) as mock_req,
            patch("rex.github_service.get_policy_engine") as mock_pe,
        ):
            mock_manager = MagicMock()
            mock_manager.get_token.return_value = "ghp_token"
            mock_cm.return_value = mock_manager

            mock_engine = MagicMock()
            mock_decision = MagicMock()
            mock_decision.allowed = True
            mock_engine.decide.return_value = mock_decision
            mock_pe.return_value = mock_engine

            service.create_commit(
                repo="alice/my-repo",
                path="README.md",
                message="Update readme",
                content="Hello world",
                branch="main",
            )

        call_kwargs = mock_req.call_args[1]
        assert call_kwargs["method"] == "PUT"
        assert "/repos/alice/my-repo/contents/README.md" in call_kwargs["url"]

    def test_create_commit_content_base64_encoded(self):
        """create_commit() base64-encodes the file content."""
        service = GitHubService()

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "commit": {"sha": "deadbeef", "html_url": "https://github.com/commit/deadbeef"}
        }
        fake_response.raise_for_status.return_value = None

        with (
            patch("rex.github_service.get_credential_manager") as mock_cm,
            patch("requests.request", return_value=fake_response) as mock_req,
            patch("rex.github_service.get_policy_engine") as mock_pe,
        ):
            mock_manager = MagicMock()
            mock_manager.get_token.return_value = "ghp_token"
            mock_cm.return_value = mock_manager

            mock_engine = MagicMock()
            mock_decision = MagicMock()
            mock_decision.allowed = True
            mock_engine.decide.return_value = mock_decision
            mock_pe.return_value = mock_engine

            service.create_commit(
                repo="alice/my-repo",
                path="file.txt",
                message="Add file",
                content="plain text",
                branch="main",
            )

        body = mock_req.call_args[1]["json"]
        expected_encoded = base64.b64encode(b"plain text").decode()
        assert body["content"] == expected_encoded

    def test_create_commit_returns_sha_and_url(self):
        """create_commit() returns dict with sha, url, and message."""
        service = GitHubService()

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "commit": {
                "sha": "sha1234",
                "html_url": "https://github.com/alice/my-repo/commit/sha1234",
            }
        }
        fake_response.raise_for_status.return_value = None

        with (
            patch("rex.github_service.get_credential_manager") as mock_cm,
            patch("requests.request", return_value=fake_response),
            patch("rex.github_service.get_policy_engine") as mock_pe,
        ):
            mock_manager = MagicMock()
            mock_manager.get_token.return_value = "ghp_token"
            mock_cm.return_value = mock_manager

            mock_engine = MagicMock()
            mock_decision = MagicMock()
            mock_decision.allowed = True
            mock_engine.decide.return_value = mock_decision
            mock_pe.return_value = mock_engine

            result = service.create_commit(
                repo="alice/my-repo",
                path="file.txt",
                message="My commit message",
                content="content here",
                branch="main",
            )

        assert result["sha"] == "sha1234"
        assert result["url"] == "https://github.com/alice/my-repo/commit/sha1234"
        assert result["message"] == "My commit message"

    def test_create_commit_sends_sha_when_updating(self):
        """create_commit() includes blob sha when updating an existing file."""
        service = GitHubService()

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "commit": {"sha": "newsha", "html_url": "https://github.com/commit/newsha"}
        }
        fake_response.raise_for_status.return_value = None

        with (
            patch("rex.github_service.get_credential_manager") as mock_cm,
            patch("requests.request", return_value=fake_response) as mock_req,
            patch("rex.github_service.get_policy_engine") as mock_pe,
        ):
            mock_manager = MagicMock()
            mock_manager.get_token.return_value = "ghp_token"
            mock_cm.return_value = mock_manager

            mock_engine = MagicMock()
            mock_decision = MagicMock()
            mock_decision.allowed = True
            mock_engine.decide.return_value = mock_decision
            mock_pe.return_value = mock_engine

            service.create_commit(
                repo="alice/my-repo",
                path="file.txt",
                message="Update file",
                content="updated content",
                branch="main",
                sha="existingblobsha",
            )

        body = mock_req.call_args[1]["json"]
        assert body["sha"] == "existingblobsha"


class TestErrorsHandled:
    """errors handled — failures raise RuntimeError and are logged."""

    def test_list_issues_api_error_raises_runtime_error(self):
        """list_issues() wraps API errors in RuntimeError."""
        service = GitHubService()

        with (
            patch("rex.github_service.get_credential_manager") as mock_cm,
            patch("requests.request", side_effect=Exception("Connection refused")),
        ):
            mock_manager = MagicMock()
            mock_manager.get_token.return_value = "ghp_token"
            mock_cm.return_value = mock_manager

            with pytest.raises(RuntimeError, match="Failed to list issues"):
                service.list_issues("alice/my-repo")

    def test_create_commit_api_error_raises_runtime_error(self):
        """create_commit() wraps API errors in RuntimeError."""
        service = GitHubService()

        with (
            patch("rex.github_service.get_credential_manager") as mock_cm,
            patch("requests.request", side_effect=Exception("Timeout")),
            patch("rex.github_service.get_policy_engine") as mock_pe,
        ):
            mock_manager = MagicMock()
            mock_manager.get_token.return_value = "ghp_token"
            mock_cm.return_value = mock_manager

            mock_engine = MagicMock()
            mock_decision = MagicMock()
            mock_decision.allowed = True
            mock_engine.decide.return_value = mock_decision
            mock_pe.return_value = mock_engine

            with pytest.raises(RuntimeError, match="Failed to create commit"):
                service.create_commit(
                    repo="alice/my-repo",
                    path="file.txt",
                    message="commit",
                    content="data",
                    branch="main",
                )

    def test_create_commit_policy_denied_raises_permission_error(self):
        """create_commit() raises PermissionError when policy denies the action."""
        with patch("rex.github_service.get_policy_engine") as mock_pe:
            mock_engine = MagicMock()
            mock_decision = MagicMock()
            mock_decision.allowed = False
            mock_decision.reason = "write operations disabled"
            mock_engine.decide.return_value = mock_decision
            mock_pe.return_value = mock_engine

            service = GitHubService()
            with pytest.raises(PermissionError, match="Policy denied commit creation"):
                service.create_commit(
                    repo="alice/my-repo",
                    path="file.txt",
                    message="commit",
                    content="data",
                    branch="main",
                )

    def test_list_issues_missing_token_raises_runtime_error(self):
        """list_issues() raises RuntimeError when GitHub token is missing."""
        service = GitHubService()

        with patch("rex.github_service.get_credential_manager") as mock_cm:
            mock_manager = MagicMock()
            mock_manager.get_token.return_value = None
            mock_cm.return_value = mock_manager

            with pytest.raises(RuntimeError, match="Failed to list issues"):
                service.list_issues("alice/my-repo")
