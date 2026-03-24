"""JSON file persistence for graphs."""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "graphs")


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def list_graphs():
    """Return list of {id, name} for all saved graphs."""
    ensure_data_dir()
    results = []
    for fname in os.listdir(DATA_DIR):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(DATA_DIR, fname)
        try:
            with open(path) as f:
                g = json.load(f)
            results.append({"id": g["id"], "name": g.get("name", "Untitled")})
        except Exception:
            continue
    return results


def load_graph(graph_id):
    path = os.path.join(DATA_DIR, f"{graph_id}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def save_graph(graph):
    ensure_data_dir()
    path = os.path.join(DATA_DIR, f"{graph['id']}.json")
    with open(path, "w") as f:
        json.dump(graph, f, indent=2)


def _iter_all_nodes(graph_like):
    """Yield all nodes recursively, including those inside HFA child_graphs."""
    for node in graph_like.get("nodes", {}).values():
        yield node
        if node.get("is_hfa") and node.get("child_graph"):
            yield from _iter_all_nodes(node["child_graph"])


def get_all_nodes_except(exclude_graph_id=None):
    """Collect all nodes from every graph except the excluded one."""
    results = []
    for summary in list_graphs():
        g = load_graph(summary["id"])
        if not g:
            continue
        is_current = (summary["id"] == exclude_graph_id)
        for node in _iter_all_nodes(g):
            entry = {
                "name": node.get("name", ""),
                "prompt": node.get("prompt", ""),
                "source_graph_name": g.get("name", "Untitled"),
                "is_current_graph": is_current,
            }
            if node.get("is_hfa") and node.get("child_graph"):
                entry["is_hfa"] = True
                entry["child_graph"] = node["child_graph"]
            results.append(entry)
    return results


def delete_graph(graph_id):
    path = os.path.join(DATA_DIR, f"{graph_id}.json")
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


# --- Working directory history (server-persisted) ---
WORKING_DIRS_PATH = os.path.join(os.path.dirname(__file__), "data", "working_dirs.json")


def load_working_dirs():
    """Load the saved working directory history list."""
    try:
        with open(WORKING_DIRS_PATH) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_working_dirs(dirs):
    """Save the working directory history list."""
    os.makedirs(os.path.dirname(WORKING_DIRS_PATH), exist_ok=True)
    with open(WORKING_DIRS_PATH, "w") as f:
        json.dump(dirs, f, indent=2)
