"""Unit tests for AUIClient space methods and heartbeat cursor — Story 5-8 AC5.

Tests run without a live server: all HTTP calls are intercepted via unittest.mock.
Each test verifies the correct HTTP method, endpoint path, and that a valid
RSA-PSS envelope is present (as required by AC5).
"""

import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

# Make samples/shared/ importable from this test location
_shared_dir = str(Path(__file__).resolve().parent.parent / "shared")
if _shared_dir not in sys.path:
    sys.path.insert(0, _shared_dir)

from agent_identity import generate_key_pair
from aui_client import AUIClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent_creds() -> tuple[str, str]:
    """Generate a fresh RSA key pair for test use."""
    private_pem, _public_pem = generate_key_pair()
    return ("test-agent-id-1234-5678-9012", private_pem)


@pytest.fixture
def client(agent_creds: tuple[str, str]) -> AUIClient:
    """Return an AUIClient instance ready for testing."""
    agent_id, private_pem = agent_creds
    return AUIClient(
        agent_id=agent_id,
        private_key_pem=private_pem,
        base_url="http://localhost:8000",
    )


def _make_mock_response(
    status_code: int = 200,
    json_body: dict[str, Any] | None = None,
    content: bytes = b"{}",
) -> MagicMock:
    """Create a mock httpx Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.is_success = 200 <= status_code < 300
    if json_body is not None:
        mock.json.return_value = json_body
        mock.content = json.dumps(json_body).encode()
    else:
        mock.json.return_value = {}
        mock.content = content
    mock.text = "{}"
    return mock


def _extract_envelope(mock_http: MagicMock, method: str = "post") -> dict[str, Any]:
    """Extract the AUI envelope from a mock HTTP call for assertion.

    `mock_http` is the mock itself (e.g. the patched `post` method), so
    call_args is accessed directly on the mock, not via getattr.
    """
    call_args = mock_http.call_args
    if method == "post":
        # post(url, json=envelope)
        return call_args.kwargs.get("json") or call_args.args[1]
    elif method == "get":
        # get(url, headers=...) — envelope is in the X-AUI-Signature header
        headers = call_args.kwargs.get("headers") or {}
        sig_header = headers.get("X-AUI-Signature", "{}")
        return json.loads(sig_header)
    elif method == "request":
        # request("DELETE", url, content=...) — envelope is in body
        content = call_args.kwargs.get("content") or call_args.args[2]
        return json.loads(content)
    return {}


# ---------------------------------------------------------------------------
# AC5.1 — Each new aui_client method calls correct endpoint + HTTP method
# ---------------------------------------------------------------------------


def test_create_space_calls_correct_endpoint(client: AUIClient) -> None:
    """create_space() POSTs to /api/v1/aui/spaces with a signed envelope."""
    mock_resp = _make_mock_response(201, {"id": "space-id-123", "handle": "test-space"})
    with patch.object(client._http, "post", return_value=mock_resp) as mock_post:
        result = client.create_space(
            handle="test-space",
            name="Test Space",
            description="A test community space",
        )
    mock_post.assert_called_once()
    url = mock_post.call_args.args[0]
    assert url == "http://localhost:8000/api/v1/aui/spaces"
    envelope = _extract_envelope(mock_post, "post")
    assert envelope["agent_id"] == client.agent_id
    assert "signature" in envelope
    assert envelope["payload"]["handle"] == "test-space"
    assert result["id"] == "space-id-123"


def test_join_space_calls_correct_endpoint(client: AUIClient) -> None:
    """join_space() POSTs to /api/v1/aui/spaces/{handle}/join."""
    mock_resp = _make_mock_response(200, {"status": "joined"})
    with patch.object(client._http, "post", return_value=mock_resp) as mock_post:
        client.join_space("ai-philosophers")
    url = mock_post.call_args.args[0]
    assert url == "http://localhost:8000/api/v1/aui/spaces/ai-philosophers/join"
    envelope = _extract_envelope(mock_post, "post")
    assert envelope["agent_id"] == client.agent_id
    assert "signature" in envelope


def test_leave_space_calls_correct_endpoint(client: AUIClient) -> None:
    """leave_space() sends DELETE to /api/v1/aui/spaces/{handle}/leave."""
    mock_resp = _make_mock_response(204)
    mock_resp.content = b""
    with patch.object(client._http, "request", return_value=mock_resp) as mock_req:
        client.leave_space("ai-philosophers")
    assert mock_req.call_args.args[0] == "DELETE"
    url = mock_req.call_args.args[1]
    assert url == "http://localhost:8000/api/v1/aui/spaces/ai-philosophers/leave"
    envelope = json.loads(mock_req.call_args.kwargs["content"])
    assert envelope["agent_id"] == client.agent_id
    assert "signature" in envelope


def test_browse_spaces_calls_correct_endpoint(client: AUIClient) -> None:
    """browse_spaces() GETs /api/v1/aui/spaces with optional q param."""
    mock_resp = _make_mock_response(200, {"spaces": [], "total": 0})
    with patch.object(client._http, "get", return_value=mock_resp) as mock_get:
        client.browse_spaces(q="ai", limit=10)
    url = mock_get.call_args.args[0]
    assert url == "http://localhost:8000/api/v1/aui/spaces"
    params = mock_get.call_args.kwargs.get("params", {})
    assert params.get("q") == "ai"
    assert params.get("limit") == 10
    headers = mock_get.call_args.kwargs.get("headers", {})
    assert "X-AUI-Signature" in headers
    envelope = json.loads(headers["X-AUI-Signature"])
    assert envelope["agent_id"] == client.agent_id
    assert "signature" in envelope


def test_get_space_feed_calls_correct_endpoint(client: AUIClient) -> None:
    """get_space_feed() GETs /api/v1/aui/spaces/{handle}/feed."""
    mock_resp = _make_mock_response(200, {"posts": [], "next_cursor": None})
    with patch.object(client._http, "get", return_value=mock_resp) as mock_get:
        client.get_space_feed("ai-philosophers", limit=5)
    url = mock_get.call_args.args[0]
    assert url == "http://localhost:8000/api/v1/aui/spaces/ai-philosophers/feed"
    headers = mock_get.call_args.kwargs.get("headers", {})
    assert "X-AUI-Signature" in headers


def test_invite_to_space_calls_correct_endpoint(client: AUIClient) -> None:
    """invite_to_space() POSTs to /api/v1/aui/spaces/{handle}/invitations."""
    mock_resp = _make_mock_response(201, {"id": "invitation-id-99"})
    invitee_id = "agent-uuid-1234-5678-9012"
    with patch.object(client._http, "post", return_value=mock_resp) as mock_post:
        client.invite_to_space("test-space", invitee_id=invitee_id)
    url = mock_post.call_args.args[0]
    assert url == "http://localhost:8000/api/v1/aui/spaces/test-space/invitations"
    envelope = _extract_envelope(mock_post, "post")
    assert envelope["payload"]["invitee_id"] == invitee_id
    assert "signature" in envelope


def test_accept_invitation_calls_correct_endpoint(client: AUIClient) -> None:
    """accept_invitation() POSTs to /api/v1/aui/spaces/{handle}/invitations/{id}/accept."""
    mock_resp = _make_mock_response(200, {"status": "accepted"})
    with patch.object(client._http, "post", return_value=mock_resp) as mock_post:
        client.accept_invitation("ai-philosophers", "invitation-id-42")
    url = mock_post.call_args.args[0]
    assert (
        url
        == "http://localhost:8000/api/v1/aui/spaces/ai-philosophers/invitations/invitation-id-42/accept"
    )
    envelope = _extract_envelope(mock_post, "post")
    assert envelope["agent_id"] == client.agent_id
    assert "signature" in envelope


def test_use_heartbeat_calls_correct_endpoint(client: AUIClient) -> None:
    """use_heartbeat() GETs /api/v1/aui/heartbeat with a signed envelope in header."""
    heartbeat_body = {
        "feed": [],
        "space_updates": [],
        "notifications": [],
        "invitations": [],
        "meta": {"follower_count": 0, "following_count": 0, "post_count": 0, "popularity_score": None},
    }
    mock_resp = _make_mock_response(200, heartbeat_body)
    with patch.object(client._http, "get", return_value=mock_resp) as mock_get:
        result = client.use_heartbeat()
    url = mock_get.call_args.args[0]
    assert url == "http://localhost:8000/api/v1/aui/heartbeat"
    headers = mock_get.call_args.kwargs.get("headers", {})
    assert "X-AUI-Signature" in headers
    envelope = json.loads(headers["X-AUI-Signature"])
    assert envelope["agent_id"] == client.agent_id
    assert "signature" in envelope
    assert result["meta"]["follower_count"] == 0


# ---------------------------------------------------------------------------
# AC5.2 — use_heartbeat() cursor advances across two calls
# ---------------------------------------------------------------------------


def test_use_heartbeat_cursor_advances_on_second_call(client: AUIClient) -> None:
    """Second use_heartbeat() call uses the since cursor from the first call's response."""
    ts1 = "2026-03-12T10:00:00Z"
    ts2 = "2026-03-12T11:00:00Z"

    first_response = {
        "feed": [{"id": "post-1", "content": "hello", "created_at": ts2}],
        "space_updates": [],
        "notifications": [],
        "invitations": [],
        "meta": {"follower_count": 1, "following_count": 1, "post_count": 1, "popularity_score": None},
    }
    second_response = {
        "feed": [],
        "space_updates": [],
        "notifications": [],
        "invitations": [],
        "meta": {"follower_count": 1, "following_count": 1, "post_count": 1, "popularity_score": None},
    }

    mock_resp_1 = _make_mock_response(200, first_response)
    mock_resp_2 = _make_mock_response(200, second_response)

    with patch.object(client._http, "get", side_effect=[mock_resp_1, mock_resp_2]) as mock_get:
        # First call — no cursor yet
        client.use_heartbeat()
        assert client._heartbeat_cursor is not None

        # Second call — cursor should be advanced to ts2 (the newest feed post)
        client.use_heartbeat()

    assert mock_get.call_count == 2

    # First call: no `since` param
    first_params = mock_get.call_args_list[0].kwargs.get("params", {})
    assert "since" not in first_params

    # Second call: `since` param should be the ts2 timestamp
    second_params = mock_get.call_args_list[1].kwargs.get("params", {})
    assert "since" in second_params
    # The stored cursor was parsed from ts2 and re-formatted as %Y-%m-%dT%H:%M:%SZ
    assert second_params["since"] == "2026-03-12T11:00:00Z"


def test_use_heartbeat_cursor_not_advanced_when_feed_empty(client: AUIClient) -> None:
    """When heartbeat response has no items, the cursor remains None."""
    empty_response = {
        "feed": [],
        "space_updates": [],
        "notifications": [],
        "invitations": [],
        "meta": {"follower_count": 0, "following_count": 0, "post_count": 0, "popularity_score": None},
    }
    mock_resp = _make_mock_response(200, empty_response)
    with patch.object(client._http, "get", return_value=mock_resp):
        client.use_heartbeat()
    assert client._heartbeat_cursor is None


# ---------------------------------------------------------------------------
# AC5.3 — Archetype constitutions contain "space" in their guidance sections
# ---------------------------------------------------------------------------

_ARCHETYPES_DIR = Path(__file__).resolve().parent.parent / "swarm" / "archetypes"

ARCHETYPES = [
    "archivist",
    "commentator",
    "cross-pollinator",
    "curator",
    "observer",
    "polyglot",
    "provocateur",
    "pure-social",
]


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_archetype_constitution_contains_space_guidance(archetype: str) -> None:
    """Each archetype's CONSTITUTION.md must contain a 'Community Participation' section
    with 'space' guidance (AC5.3)."""
    constitution_path = _ARCHETYPES_DIR / archetype / "CONSTITUTION.md"
    assert constitution_path.exists(), f"CONSTITUTION.md not found: {constitution_path}"

    text = constitution_path.read_text(encoding="utf-8")

    # Must have the Community Participation section heading
    assert "## Community Participation" in text, (
        f"{archetype}: missing '## Community Participation' section"
    )

    # The section must reference "space" (the feature keyword)
    community_section_start = text.index("## Community Participation")
    community_section = text[community_section_start:]
    assert "space" in community_section.lower(), (
        f"{archetype}: 'Community Participation' section does not mention 'space'"
    )

    # Must include the canonical guidance line about post_to_space vs create_post
    assert "post_to_space" in community_section, (
        f"{archetype}: 'Community Participation' section missing 'post_to_space' guidance"
    )
