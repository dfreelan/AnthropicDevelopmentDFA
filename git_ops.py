"""Git operations and timeline management for DFA undo/redo."""

import json
import os
import subprocess
import threading
from datetime import datetime

CLAUDE_BIN = "PLACEHOLDER"
TIMELINES_DIR = os.path.join(os.path.dirname(__file__), "data", "timelines")

_timeline_lock = threading.Lock()


# --- Low-level git helpers ---

def is_git_repo(cwd):
    """Check if cwd is inside a git repo."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, cwd=cwd, timeout=10,
        )
        return r.returncode == 0 and r.stdout.strip() == "true"
    except Exception:
        return False


def has_changes(cwd):
    """Check if there are uncommitted changes (staged, unstaged, or untracked)."""
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=cwd, timeout=15,
        )
        return r.returncode == 0 and r.stdout.strip() != ""
    except Exception:
        return False


def get_current_sha(cwd):
    """Get the current HEAD commit SHA."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=cwd, timeout=10,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def auto_commit(cwd, message):
    """Stage all changes and commit. Returns new SHA or None."""
    try:
        subprocess.run(
            ["git", "add", "-A"],
            capture_output=True, text=True, cwd=cwd, timeout=30,
        )
        r = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True, text=True, cwd=cwd, timeout=30,
        )
        if r.returncode == 0:
            return get_current_sha(cwd)
        # If nothing to commit, return current HEAD
        return get_current_sha(cwd)
    except Exception:
        return None


def generate_commit_message(cwd, context_hint=""):
    """Use Claude to generate a commit message from the current diff. Falls back to default."""
    try:
        r = subprocess.run(
            ["git", "diff", "--stat"],
            capture_output=True, text=True, cwd=cwd, timeout=15,
        )
        diff_stat = r.stdout.strip() if r.returncode == 0 else ""

        # Also get untracked files
        r2 = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=cwd, timeout=15,
        )
        status = r2.stdout.strip() if r2.returncode == 0 else ""

        if not diff_stat and not status:
            return "DFA auto-snapshot (no changes)"

        # Combine info, truncate to ~4000 chars
        info = f"Diff stat:\n{diff_stat}\n\nStatus:\n{status}"
        if len(info) > 4000:
            info = info[:4000] + "\n... (truncated)"

        prompt = f"Generate a short (1 line, max 72 chars) git commit message for these changes. Output ONLY the message, nothing else.\n\n{info}\n\nContext: {context_hint}"

        env = os.environ.copy()
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "128000"
        r3 = subprocess.run(
            [CLAUDE_BIN, "--dangerously-skip-permissions", "-p"],
            input=prompt,
            capture_output=True, text=True, cwd=cwd, timeout=120, env=env,
        )
        if r3.returncode == 0 and r3.stdout.strip():
            msg = r3.stdout.strip()
            # Clean up: take first line, cap at 120 chars
            msg = msg.split("\n")[0].strip().strip('"').strip("'")
            if len(msg) > 120:
                msg = msg[:117] + "..."
            if msg:
                return msg
    except Exception:
        pass
    return "DFA auto-snapshot"


# --- Timeline JSON management ---

def _timeline_path(graph_id):
    """Get path to timeline JSON file for a graph."""
    os.makedirs(TIMELINES_DIR, exist_ok=True)
    return os.path.join(TIMELINES_DIR, f"{graph_id}.json")


def load_timeline(graph_id):
    """Load timeline from disk. Returns dict with snapshots and current_index."""
    path = _timeline_path(graph_id)
    with _timeline_lock:
        try:
            with open(path, "r") as f:
                data = json.load(f)
                if "snapshots" not in data:
                    data["snapshots"] = []
                if "current_index" not in data:
                    data["current_index"] = len(data["snapshots"]) - 1
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            return {"snapshots": [], "current_index": -1}


def save_timeline(graph_id, timeline):
    """Write timeline to disk."""
    path = _timeline_path(graph_id)
    with _timeline_lock:
        os.makedirs(TIMELINES_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump(timeline, f, indent=2)


def add_snapshot(graph_id, sha, label):
    """Add a new snapshot to the timeline. Truncates forward history if not at end."""
    timeline = load_timeline(graph_id)
    idx = timeline["current_index"]

    # Truncate forward history (like normal undo behavior)
    if idx < len(timeline["snapshots"]) - 1:
        timeline["snapshots"] = timeline["snapshots"][:idx + 1]

    # Don't add duplicate consecutive SHAs
    if timeline["snapshots"] and timeline["snapshots"][-1]["sha"] == sha:
        return

    timeline["snapshots"].append({
        "sha": sha,
        "label": label,
        "timestamp": datetime.now().isoformat(),
    })
    timeline["current_index"] = len(timeline["snapshots"]) - 1
    save_timeline(graph_id, timeline)


def get_timeline_state(graph_id):
    """Get timeline state for the API."""
    timeline = load_timeline(graph_id)
    total = len(timeline["snapshots"])
    idx = timeline["current_index"]
    return {
        "snapshots": timeline["snapshots"],
        "current_index": idx,
        "can_undo": idx > 0,
        "can_redo": idx < total - 1,
        "total": total,
    }


# --- Undo/Redo ---

def _restore_to_sha(cwd, target_sha):
    """Restore working tree to match a given SHA without moving HEAD.

    Strategy: checkout all files from target SHA, then remove files
    that exist in current tree but not in target.
    """
    # Get files at target SHA
    r1 = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", target_sha],
        capture_output=True, text=True, cwd=cwd, timeout=30,
    )
    target_files = set(r1.stdout.strip().split("\n")) if r1.returncode == 0 and r1.stdout.strip() else set()

    # Get files currently tracked at HEAD
    r2 = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        capture_output=True, text=True, cwd=cwd, timeout=30,
    )
    current_files = set(r2.stdout.strip().split("\n")) if r2.returncode == 0 and r2.stdout.strip() else set()

    # Remove files that are in current but not in target
    to_remove = current_files - target_files
    for f in to_remove:
        subprocess.run(
            ["git", "rm", "-f", f],
            capture_output=True, text=True, cwd=cwd, timeout=10,
        )

    # Restore all files from target SHA
    if target_files:
        subprocess.run(
            ["git", "checkout", target_sha, "--", "."],
            capture_output=True, text=True, cwd=cwd, timeout=30,
        )

    # Stage everything
    subprocess.run(
        ["git", "add", "-A"],
        capture_output=True, text=True, cwd=cwd, timeout=30,
    )


def undo(graph_id, cwd):
    """Undo to the previous snapshot."""
    timeline = load_timeline(graph_id)
    if timeline["current_index"] <= 0:
        return {"error": "Nothing to undo"}

    timeline["current_index"] -= 1
    snapshot = timeline["snapshots"][timeline["current_index"]]

    try:
        _restore_to_sha(cwd, snapshot["sha"])
        # Commit the restoration so it's a clean state
        auto_commit(cwd, f"Undo to: {snapshot['label']}")
    except Exception as e:
        return {"error": f"Failed to restore: {e}"}

    save_timeline(graph_id, timeline)
    return {"success": True, "snapshot": snapshot}


def redo(graph_id, cwd):
    """Redo to the next snapshot."""
    timeline = load_timeline(graph_id)
    if timeline["current_index"] >= len(timeline["snapshots"]) - 1:
        return {"error": "Nothing to redo"}

    timeline["current_index"] += 1
    snapshot = timeline["snapshots"][timeline["current_index"]]

    try:
        _restore_to_sha(cwd, snapshot["sha"])
        # Commit the restoration so it's a clean state
        auto_commit(cwd, f"Redo to: {snapshot['label']}")
    except Exception as e:
        return {"error": f"Failed to restore: {e}"}

    save_timeline(graph_id, timeline)
    return {"success": True, "snapshot": snapshot}
