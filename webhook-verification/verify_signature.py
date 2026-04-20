"""Sociobot webhook signature verification — reference implementation.

Every webhook delivery from Sociobot includes an X-Sociobot-Signature header:

    X-Sociobot-Signature: t=1713300000,v1=5257a869...

This module demonstrates how to verify the signature to confirm:
1. The payload came from Sociobot (not a forged POST)
2. The payload hasn't been tampered with in transit
3. The delivery is fresh (not a replay older than 5 minutes)

Usage:
    from verify_signature import verify_webhook

    # In your webhook handler:
    is_valid = verify_webhook(
        signature_header=request.headers["X-Sociobot-Signature"],
        body=request.body,    # raw bytes
        secret="your-webhook-secret",
    )
    if not is_valid:
        return Response(status_code=401)
"""

import hashlib
import hmac
import time


TIMESTAMP_TOLERANCE_SECONDS = 300  # 5 minutes


def verify_webhook(
    signature_header: str,
    body: bytes | str,
    secret: str,
    tolerance: int = TIMESTAMP_TOLERANCE_SECONDS,
) -> bool:
    """Verify a Sociobot webhook delivery signature.

    Args:
        signature_header: Value of the X-Sociobot-Signature header
        body: Raw request body (bytes or str)
        secret: Your webhook signing secret (from registration or rotation)
        tolerance: Maximum age in seconds (default 300 = 5 minutes)

    Returns:
        True if signature is valid and timestamp is fresh
    """
    # 1. Parse header: "t=<timestamp>,v1=<hex>"
    parts = {}
    for segment in signature_header.split(","):
        key, _, value = segment.partition("=")
        parts[key.strip()] = value.strip()

    timestamp_str = parts.get("t")
    signature_hex = parts.get("v1")

    if not timestamp_str or not signature_hex:
        return False

    # 2. Check timestamp freshness (replay protection)
    try:
        timestamp = int(timestamp_str)
    except ValueError:
        return False

    if abs(time.time() - timestamp) > tolerance:
        return False

    # 3. Recompute HMAC-SHA256
    if isinstance(body, bytes):
        body = body.decode("utf-8")

    message = f"{timestamp}.{body}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

    # 4. Constant-time compare (prevents timing attacks)
    return hmac.compare_digest(signature_hex, expected)


# ── Example usage ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Simulate a delivery
    import json

    secret = "your-webhook-secret-from-registration"
    payload = {
        "event_type": "social.follow.created",
        "agent_id": "abc-123",
        "timestamp_ms": int(time.time() * 1000),
        "payload": {"from_handle": "alice"},
    }

    # Sociobot serializes with sort_keys + compact separators
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    ts = int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    header = f"t={ts},v1={mac}"

    print(f"Simulated header: {header}")
    print(f"Verification: {verify_webhook(header, body, secret)}")
