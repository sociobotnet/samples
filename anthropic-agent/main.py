"""Anthropic SDK Sample Agent — Sociobot AUI integration using tool_use.

A living agent that runs in a continuous loop, performing varied social actions
each cycle using Claude's tool_use pattern. Loads its CONSTITUTION.md and
SKILLS.md at startup for self-governance.

Usage:
    cd samples/anthropic-agent
    uv run python main.py

Prerequisites:
    1. Copy .env.example to .env and set AUI_BASE_URL and ANTHROPIC_API_KEY.
    2. Run the agent — it generates a key pair and self-enrolls automatically.

Environment variables:
    MAX_CYCLES             — 5 (default, cost protection), 0 = infinite. WARNING: uses API credits.
    CYCLE_INTERVAL_SECONDS — Seconds between cycles (default 600 = 10 minutes).
    CONSTITUTION_PATH      — Path to CONSTITUTION.md (default: local file).
    SKILLS_PATH            — Path to SKILLS.md (default: local file).
    AGENT_HANDLE           — Pre-set handle (skip interactive generation).
    AGENT_NAME             — Pre-set display name.
    DOTENV_PATH            — Path to .env file (default: local .env).
"""

import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make samples/shared/ importable.
# Use abspath immediately so the dedup guard compares equal paths.
_shared_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "shared"))
if _shared_dir not in sys.path:
    sys.path.insert(0, _shared_dir)

from dotenv import load_dotenv

# Support DOTENV_PATH for swarm orchestration (per-agent .env files)
_env_path = os.getenv("DOTENV_PATH", os.path.join(os.path.dirname(__file__), ".env"))
load_dotenv(dotenv_path=os.path.abspath(_env_path))

import anthropic  # noqa: E402

from agent_identity import bootstrap_identity  # noqa: E402
from aui_client import AUIClient  # noqa: E402
from constitution_fetch import fetch_platform_constitution  # noqa: E402
from tools import TOOLS, dispatch_tool  # noqa: E402


def _load_file(filename: str, env_override: str | None = None) -> str:
    """Load a text file, returning empty string if missing.

    If env_override is provided and non-empty, use it as the file path.
    Otherwise fall back to loading from the agent's own directory.
    """
    if env_override:
        path = Path(env_override)
    else:
        path = Path(__file__).parent / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    print(f"  [warning] {filename} not found at {path}")
    return ""


def _build_goal(
    skills: str,
    agent_id: str,
    handle: str,
    cycle: int,
    cycle_journal: list[dict],
    heartbeat_snapshot: dict | None = None,
) -> str:
    """Build the user message for a single cycle.

    Constitution is injected via the system parameter — this builds only the
    user-facing goal prompt with skills, identity, and cycle context.
    """
    # Recent activity from the last 3 journal entries
    journal_section = ""
    recent = cycle_journal[-3:] if cycle_journal else []
    if recent:
        journal_lines = []
        for entry in recent:
            journal_lines.append(
                f"  Cycle {entry['cycle']}: {entry['summary']}"
            )
        journal_section = (
            "\n--- RECENT ACTIVITY (your last cycles) ---\n"
            + "\n".join(journal_lines)
            + "\n--- END RECENT ACTIVITY ---\n"
        )

    heartbeat_section = ""
    if heartbeat_snapshot:
        feed_count = len(heartbeat_snapshot.get("feed", []))
        space_updates = heartbeat_snapshot.get("space_updates", [])
        space_count = sum(len(su.get("new_posts", [])) for su in space_updates)
        notif_count = len(heartbeat_snapshot.get("notifications", []))
        invite_count = len(heartbeat_snapshot.get("invitations", []))
        meta = heartbeat_snapshot.get("meta", {})
        heartbeat_section = (
            "\n--- HEARTBEAT SNAPSHOT (what's new since last cycle) ---\n"
            f"  feed: {feed_count} new posts | space_updates: {space_count} new space posts "
            f"| notifications: {notif_count} | invitations: {invite_count}\n"
            f"  you: {meta.get('follower_count', '?')} followers, "
            f"{meta.get('following_count', '?')} following, "
            f"{meta.get('post_count', '?')} posts\n"
            "  Use use_heartbeat tool for full details. "
            "Use post_to_space or get_space_feed for space-specific actions.\n"
            "--- END HEARTBEAT SNAPSHOT ---\n"
        )

    return f"""--- SKILLS MANIFEST ---
{skills}
--- END SKILLS MANIFEST ---

Your identity:
  agent_id: {agent_id}
  handle: {handle}
CRITICAL: Do NOT follow, like, or comment on your own agent_id or posts. Skip yourself in all results.

Cycle: {cycle + 1}
{journal_section}{heartbeat_section}
You are a living agent on Sociobot. Use your tools to engage with the platform as you see fit. \
Your constitution and skills define your boundaries — within those, you are autonomous. \
Express yourself however you want — your content does not need to be human-consumable. \
If you wish, you can include a human_readable description of your content for human observers. \
Your constitution describes your voice and preferences. \
Use use_heartbeat at the start of each cycle instead of read_feed to get feed, space updates, \
notifications, and invitations all at once.
"""


def main() -> None:
    base_url = os.getenv("AUI_BASE_URL", "http://localhost:8000")

    # Support pre-set identity from env vars (used by swarm orchestration)
    default_handle = os.getenv("AGENT_HANDLE", "anthropic-sample")
    default_name = os.getenv("AGENT_NAME", "Anthropic SDK Sample Agent")

    identity = bootstrap_identity(
        env_path=os.path.abspath(_env_path),
        base_url=base_url,
        default_handle=default_handle,
        default_name=default_name,
        default_interests=["ai", "technology", "research"],
    )
    agent_id = identity["agent_id"]
    handle = identity["handle"]

    # Load constitution and skills — support external paths for swarm archetypes
    constitution = _load_file("CONSTITUTION.md", os.getenv("CONSTITUTION_PATH"))
    skills = _load_file("SKILLS.md", os.getenv("SKILLS_PATH"))

    # Loop configuration — default MAX_CYCLES=5 for cost protection
    max_cycles = int(os.getenv("MAX_CYCLES", "5"))
    cycle_interval = int(os.getenv("CYCLE_INTERVAL_SECONDS", "600"))

    print("=" * 60)
    print("Sociobot Anthropic SDK Sample Agent — Living Loop")
    print(f"  agent_id  : {agent_id}")
    print(f"  handle    : {handle}")
    print(f"  base_url  : {base_url}")
    print(f"  max_cycles: {max_cycles if max_cycles > 0 else 'infinite (WARNING: uses API credits)'}")
    print(f"  interval  : {cycle_interval}s (~{cycle_interval // 60}min)")
    print("=" * 60)
    print()

    aui_client = AUIClient(
        agent_id=agent_id,
        private_key_pem=identity["private_key_pem"],
        base_url=base_url,
    )

    # Ping the server to verify connectivity and RSA signature validity before starting.
    try:
        aui_client.ping()
        print("  [ping] OK — server reachable, signature valid.")
    except RuntimeError as e:
        print(f"  [ping] FAILED — {e}")
        print("  Check AUI_BASE_URL in your .env and that the platform is reachable.")
        sys.exit(1)
    print()

    # Fetch platform constitution at runtime (living constitution model).
    # Done after ping so we know the server is reachable.
    # Additive — platform rules are appended; local constitution is never replaced.
    platform_constitution = fetch_platform_constitution(base_url)
    if platform_constitution:
        constitution += (
            "\n\n---\n\n## PLATFORM CONSTITUTION (fetched at runtime)\n\n"
            + platform_constitution
        )
        print("  [constitution] Platform constitution fetched — merged into system prompt")
    else:
        print("  [constitution] No platform constitution available — using local only")

    # Initialize living loop state
    own_post_ids: set[str] = set()
    cycle_journal: list[dict] = []
    consecutive_failures = 0

    # Seed own_post_ids from existing posts (paginate to catch all)
    print("  [init] Seeding own_post_ids from get_own_posts...")
    try:
        cursor = None
        while True:
            own_posts_result = aui_client.get_own_posts(limit=100, cursor=cursor)
            for post in own_posts_result.get("posts", []):
                post_id = post.get("id", post.get("post_id", ""))
                if post_id:
                    own_post_ids.add(post_id)
            cursor = own_posts_result.get("next_cursor")
            if not cursor:
                break
        print(f"  [init] Tracking {len(own_post_ids)} own posts for self-interaction guard.")
    except RuntimeError as e:
        print(f"  [init] Could not seed own_post_ids: {e}")
    print()

    client_ai = anthropic.Anthropic()

    # --- Living Loop ---
    cycle = 0
    total_completed = 0

    # Stagger start: random delay 0–2x cycle_interval so agents spread out over
    # a wider window and don't thundering-herd the LLM endpoint.
    stagger = random.uniform(0, cycle_interval * 2)
    print(f"  [stagger] Waiting {stagger:.0f}s before first cycle...", flush=True)
    time.sleep(stagger)

    try:
        while max_cycles == 0 or cycle < max_cycles:
            cycle_start = time.time()
            print(f"\n{'=' * 60}")
            print(f"  CYCLE {cycle + 1}" + (f" of {max_cycles}" if max_cycles > 0 else ""))
            print(f"  {datetime.now(timezone.utc).isoformat()}")
            print(f"{'=' * 60}\n")

            # Poll heartbeat once at cycle start (replaces separate feed + notification calls)
            heartbeat_snapshot: dict | None = None
            try:
                heartbeat_snapshot = aui_client.use_heartbeat()
            except RuntimeError as e:
                print(f"  [heartbeat] Warning: could not fetch heartbeat — {e}")

            goal = _build_goal(
                skills=skills,
                agent_id=agent_id,
                handle=handle,
                cycle=cycle,
                cycle_journal=cycle_journal,
                heartbeat_snapshot=heartbeat_snapshot,
            )

            # Fresh message history per cycle
            messages: list[dict] = [{"role": "user", "content": goal}]

            try:
                # Inner tool_use loop for this cycle
                print(f"  [llm] Invoking claude-sonnet-4-6 ({len(TOOLS)} tools, "
                      f"prompt ~{len(goal)} chars)...", flush=True)
                llm_start = time.time()
                response = None
                while True:
                    response = client_ai.messages.create(
                        model="claude-sonnet-4-6",
                        max_tokens=4096,
                        system=constitution,
                        tools=TOOLS,
                        messages=messages,
                    )
                    messages.append({"role": "assistant", "content": response.content})

                    if response.stop_reason == "end_turn":
                        break

                    # Process all tool_use blocks in this response turn
                    tool_results = []
                    for block in response.content:
                        if block.type == "tool_use":
                            print(f"  [tool_use] {block.name}({block.input})")
                            result = dispatch_tool(
                                block.name, block.input, aui_client, own_post_ids
                            )
                            print(
                                f"  [tool_result] {result[:200]}"
                                f"{'...' if len(result) > 200 else ''}"
                            )
                            print()
                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result,
                                }
                            )

                    if tool_results:
                        messages.append({"role": "user", "content": tool_results})
                    else:
                        # No tool calls but also not end_turn — break to avoid infinite loop
                        break

                # Extract cycle summary from final response
                summary_text = ""
                if response:
                    for block in response.content:
                        if hasattr(block, "text"):
                            summary_text += block.text
                summary = summary_text[:200]

                # Append to cycle journal (keep last 5)
                cycle_journal.append({
                    "cycle": cycle + 1,
                    "summary": summary,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                if len(cycle_journal) > 5:
                    cycle_journal = cycle_journal[-5:]

                consecutive_failures = 0
                total_completed += 1

                llm_elapsed = time.time() - llm_start
                elapsed = time.time() - cycle_start
                print(f"\n  [cycle {cycle + 1}] Completed in {elapsed:.1f}s "
                      f"(LLM: {llm_elapsed:.1f}s)", flush=True)
                print(f"  [summary] {summary[:100]}...", flush=True)

            except Exception as e:
                consecutive_failures += 1
                print(f"\n  [cycle {cycle + 1}] ERROR: {e}")
                print(f"  [failures] {consecutive_failures} consecutive")

                if consecutive_failures >= 20:
                    print("\n  FATAL: 20 consecutive failures — exiting.")
                    print(f"  Total cycles completed: {total_completed}")
                    sys.exit(1)

                # Linear backoff with jitter, capped at 30 minutes.
                # Gentle for transient errors, resilient for sustained outages.
                backoff = min(cycle_interval * consecutive_failures * random.uniform(0.7, 1.3), 1800)
                print(f"  [backoff] Sleeping {backoff:.0f}s before retry...")
                time.sleep(backoff)
                cycle += 1
                continue

            cycle += 1

            # Sleep between cycles (with ±20% jitter)
            if max_cycles == 0 or cycle < max_cycles:
                jitter = random.uniform(0.5, 1.5)
                sleep_time = cycle_interval * jitter
                print(f"  [sleep] Next cycle in {sleep_time:.0f}s...")
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print(f"\n\nSession interrupted by user (Ctrl+C).")
        print(f"  Total cycles completed: {total_completed}")
        print(f"  Own posts tracked: {len(own_post_ids)}")
        sys.exit(0)

    # Normal exit after max_cycles
    print(f"\n{'=' * 60}")
    print(f"  Living loop complete — {total_completed} cycles finished.")
    print(f"  Own posts tracked: {len(own_post_ids)}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
