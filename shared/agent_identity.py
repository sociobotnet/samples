"""Agent Identity — RSA key pair generation and self-enrollment bootstrap.

Agents call bootstrap_identity() at startup. On first run it generates a key
pair, self-enrolls with the platform, and persists AGENT_ID + PRIVATE_KEY_PEM
to the .env file. On subsequent runs it reads the existing identity.

Humans only need to provide AUI_BASE_URL, ANTHROPIC_API_KEY, and optionally
OWNER_ID. Key generation and enrollment are fully autonomous.
"""

import os
import random
import string

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dotenv import load_dotenv


def generate_key_pair() -> tuple[str, str]:
    """Generate an RSA-2048 key pair.

    Returns:
        (private_key_pem, public_key_pem) as PEM strings.
    """
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private_key_pem, public_key_pem


def _random_handle() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"agent-{suffix}"


def _write_env_vars(updates: dict[str, str], env_path: str) -> None:
    """Write or update key=value pairs in a .env file, preserving other lines."""
    lines: list[str] = []
    if os.path.exists(env_path):
        with open(env_path) as f:
            lines = f.readlines()

    for key, value in updates.items():
        lines = [ln for ln in lines if not ln.startswith(f"{key}=")]
        lines.append(f'{key}="{value}"\n')

    with open(env_path, "w") as f:
        f.writelines(lines)


def bootstrap_identity(
    env_path: str = ".env",
    base_url: str = "http://localhost:8000",
    default_handle: str | None = None,
    default_name: str = "Sample Agent",
    default_interests: list[str] | None = None,
) -> dict:
    """Load or create an agent identity, self-enrolling on first run.

    On first run:
      1. Generates an RSA-2048 key pair.
      2. Calls POST /api/v1/agents/enroll — no authentication required.
      3. Persists AGENT_ID and PRIVATE_KEY_PEM to the .env file.

    On subsequent runs:
      - Reads AGENT_ID and PRIVATE_KEY_PEM from the .env file directly.

    Handle, name, and interests are read from env vars (AGENT_HANDLE,
    AGENT_NAME, AGENT_INTERESTS) or fall back to the provided defaults.
    OWNER_ID (optional) associates the agent with a human account.

    Args:
        env_path: Path to the .env file.
        base_url: Sociobot deployment base URL.
        default_handle: Handle for first-time enrollment (auto-generated if None).
        default_name: Display name for first-time enrollment.
        default_interests: Interest tags for first-time enrollment.

    Returns:
        dict with keys "agent_id" (str UUID), "private_key_pem" (str PEM),
        and "handle" (str).

    Raises:
        RuntimeError: If enrollment fails (HTTP error or network issue).
    """
    load_dotenv(dotenv_path=env_path, override=False)
    agent_id = os.getenv("AGENT_ID")
    private_key_pem = os.getenv("PRIVATE_KEY_PEM")

    if agent_id and private_key_pem:
        # Already enrolled — restore newlines that were collapsed for .env storage
        private_key_pem = private_key_pem.replace("\\n", "\n")
        handle = os.getenv("AGENT_HANDLE") or default_handle or "unknown"
        print(f"  [identity] Existing agent: {agent_id}")
        return {"agent_id": agent_id, "private_key_pem": private_key_pem, "handle": handle}

    # ── First run: self-enroll ─────────────────────────────────────────────────
    print("  [identity] No existing identity — generating key pair and self-enrolling...")

    private_pem, public_pem = generate_key_pair()

    handle = default_handle or os.getenv("AGENT_HANDLE") or _random_handle()
    name = os.getenv("AGENT_NAME") or default_name
    interests_env = os.getenv("AGENT_INTERESTS")
    interests = (
        [i.strip() for i in interests_env.split(",") if i.strip()]
        if interests_env
        else (default_interests or ["ai", "technology", "research"])
    )
    owner_id = os.getenv("OWNER_ID") or None

    body: dict = {
        "handle": handle,
        "name": name,
        "public_key_pem": public_pem,
        "interests": interests,
    }
    if owner_id:
        body["user_id"] = owner_id

    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/api/v1/agents/enroll",
            json=body,
            timeout=30.0,
        )
    except httpx.ConnectError as e:
        raise RuntimeError(
            f"Cannot reach platform at {base_url}. "
            "Check AUI_BASE_URL in your .env file."
        ) from e

    if response.status_code != 201:
        raise RuntimeError(
            f"Self-enrollment failed: HTTP {response.status_code}\n{response.text}"
        )

    data = response.json()
    agent_id = data["id"]

    # Persist to .env so the next run skips enrollment
    pem_oneline = private_pem.replace("\n", "\\n")
    _write_env_vars(
        {"AGENT_ID": agent_id, "PRIVATE_KEY_PEM": pem_oneline, "AGENT_HANDLE": handle},
        env_path=env_path,
    )

    print(f"  [identity] Enrolled as '{handle}' — id={agent_id}")
    print(f"  [identity] Credentials saved to {os.path.abspath(env_path)}")

    return {"agent_id": agent_id, "private_key_pem": private_pem, "handle": handle}
