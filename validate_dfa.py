#!/usr/bin/env python3
"""DFA Graph JSON Validator"""

import json
import uuid
import sys
from collections import deque

FILE_PATH = "PLACEHOLDER"

passed = 0
failed = 0
failures = []

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        msg = f"{name}: {detail}" if detail else name
        failures.append(msg)
        print(f"  [FAIL] {name}" + (f" -- {detail}" if detail else ""))

def main():
    global passed, failed

    # ── STRUCTURAL CHECKS ──
    print("=" * 70)
    print("STRUCTURAL CHECKS")
    print("=" * 70)

    # 1. Valid JSON
    try:
        with open(FILE_PATH, "r") as f:
            data = json.load(f)
        check("Valid JSON", True)
    except json.JSONDecodeError as e:
        check("Valid JSON", False, str(e))
        print("\nCannot continue without valid JSON.")
        sys.exit(1)
    except FileNotFoundError:
        print(f"[FATAL] File not found: {FILE_PATH}")
        sys.exit(1)

    # 2. Top-level fields
    required_top = ["id", "name", "start_node", "nodes"]
    for field in required_top:
        check(f"Top-level field '{field}' exists", field in data,
              f"Missing field: {field}")

    if not all(f in data for f in required_top):
        print("\nCannot continue without required top-level fields.")
        sys.exit(1)

    # 3. id is valid UUID4
    try:
        parsed_uuid = uuid.UUID(data["id"], version=4)
        check("'id' is valid UUID4", str(parsed_uuid) == data["id"].lower())
    except (ValueError, AttributeError):
        check("'id' is valid UUID4", False, f"Value: {data.get('id')}")

    # 4. nodes is non-empty dict
    nodes = data["nodes"]
    check("'nodes' is a dict", isinstance(nodes, dict))
    check("'nodes' is non-empty", len(nodes) > 0 if isinstance(nodes, dict) else False,
          f"Length: {len(nodes) if isinstance(nodes, dict) else 'N/A'}")

    # 5. start_node UUID exists in nodes dict
    start_node = data["start_node"]
    check("'start_node' exists in nodes dict", start_node in nodes,
          f"start_node={start_node}")

    # ── NODE NAME MAP ──
    print("\n" + "=" * 70)
    print("NODE NAME MAP")
    print("=" * 70)
    node_name_to_id = {}
    node_id_to_name = {}
    for nid, ndata in nodes.items():
        name = ndata.get("name", "<NO NAME>")
        node_name_to_id[name] = nid
        node_id_to_name[nid] = name
        print(f"  {nid}  ->  \"{name}\"")

    # ── PER-NODE CHECKS ──
    print("\n" + "=" * 70)
    print("PER-NODE CHECKS")
    print("=" * 70)

    required_node_fields = ["id", "name", "prompt", "position", "transitions",
                            "persistent_context", "is_hfa"]

    all_transition_target_names = []

    for node_key, node in nodes.items():
        node_name = node.get("name", node_key)
        print(f"\n  --- Node: \"{node_name}\" (key: {node_key}) ---")

        # Required fields
        for field in required_node_fields:
            check(f"  Node '{node_name}' has field '{field}'", field in node,
                  f"Missing field: {field}")

        # Key matches id
        node_id = node.get("id", None)
        check(f"  Node '{node_name}' dict key matches node 'id'",
              node_key == node_id,
              f"key={node_key}, id={node_id}")

        # Position has numeric x and y
        pos = node.get("position", {})
        if isinstance(pos, dict):
            has_x = "x" in pos and isinstance(pos["x"], (int, float))
            has_y = "y" in pos and isinstance(pos["y"], (int, float))
            check(f"  Node '{node_name}' position has numeric 'x'", has_x,
                  f"x={pos.get('x')}")
            check(f"  Node '{node_name}' position has numeric 'y'", has_y,
                  f"y={pos.get('y')}")
        else:
            check(f"  Node '{node_name}' position is a dict", False,
                  f"type={type(pos).__name__}")

        # Transitions is an array
        transitions = node.get("transitions", None)
        check(f"  Node '{node_name}' transitions is an array",
              isinstance(transitions, list),
              f"type={type(transitions).__name__}" if transitions is not None else "Missing")

        if isinstance(transitions, list):
            # Per-transition checks
            labels_seen = set()
            for ti, tr in enumerate(transitions):
                tr_label = tr.get("label", f"<index {ti}>")

                # Required transition fields
                tr_required = ["id", "label", "target_state", "data_template"]
                for tf in tr_required:
                    check(f"  Node '{node_name}' transition '{tr_label}' has '{tf}'",
                          tf in tr, f"Missing field: {tf}")

                # target_state matches an actual node NAME
                target = tr.get("target_state", None)
                all_transition_target_names.append(target)
                check(f"  Node '{node_name}' transition '{tr_label}' target_state matches a node name",
                      target in node_name_to_id,
                      f"target_state='{target}', known names={list(node_name_to_id.keys())}")

                # Duplicate label check
                if tr_label in labels_seen:
                    check(f"  Node '{node_name}' no duplicate label '{tr_label}'",
                          False, "Duplicate transition label")
                else:
                    labels_seen.add(tr_label)

            # Report duplicate check pass if no duplicates found
            if len(labels_seen) == len(transitions):
                check(f"  Node '{node_name}' no duplicate transition labels", True)

    # ── TOPOLOGY CHECKS ──
    print("\n" + "=" * 70)
    print("TOPOLOGY CHECKS")
    print("=" * 70)

    # start_node exists and is reachable (trivially true since it's the start)
    check("Start node exists", start_node in nodes)

    # At least one terminal node (empty transitions)
    terminal_nodes = []
    non_terminal_nodes = []
    for nid, node in nodes.items():
        tr = node.get("transitions", [])
        if isinstance(tr, list) and len(tr) == 0:
            terminal_nodes.append(node.get("name", nid))
        else:
            non_terminal_nodes.append(node.get("name", nid))

    check("At least one terminal node (empty transitions)",
          len(terminal_nodes) > 0,
          f"Terminal nodes: {terminal_nodes}")
    print(f"    Terminal nodes ({len(terminal_nodes)}): {terminal_nodes}")
    print(f"    Non-terminal nodes ({len(non_terminal_nodes)}): {non_terminal_nodes}")

    # All non-terminal nodes have at least one transition
    for nid, node in nodes.items():
        tr = node.get("transitions", [])
        name = node.get("name", nid)
        if name not in terminal_nodes:
            check(f"Non-terminal node '{name}' has >= 1 transition",
                  isinstance(tr, list) and len(tr) > 0)

    # BFS reachability from start_node following transitions
    visited = set()
    queue = deque([start_node])
    visited.add(start_node)

    while queue:
        current_id = queue.popleft()
        current_node = nodes.get(current_id, {})
        transitions = current_node.get("transitions", [])
        if isinstance(transitions, list):
            for tr in transitions:
                target_name = tr.get("target_state", "")
                target_id = node_name_to_id.get(target_name)
                if target_id and target_id not in visited:
                    visited.add(target_id)
                    queue.append(target_id)

    all_node_ids = set(nodes.keys())
    orphaned = all_node_ids - visited
    check("No orphaned nodes (all reachable from start via BFS)",
          len(orphaned) == 0,
          f"Orphaned node IDs: {[node_id_to_name.get(o, o) for o in orphaned]}" if orphaned else "")

    if orphaned:
        for o in orphaned:
            print(f"    ORPHANED: \"{node_id_to_name.get(o, o)}\" ({o})")
    else:
        print(f"    All {len(visited)} nodes reachable from start node.")

    # ── PROMPT QUALITY CHECKS ──
    print("\n" + "=" * 70)
    print("PROMPT QUALITY CHECKS")
    print("=" * 70)

    for nid, node in nodes.items():
        name = node.get("name", nid)
        prompt = node.get("prompt", "")
        prompt_len = len(prompt) if isinstance(prompt, str) else 0

        print(f"\n  --- Node: \"{name}\" ---")
        print(f"    Prompt length: {prompt_len} characters")

        check(f"  Node '{name}' prompt >= 100 chars",
              prompt_len >= 100,
              f"Length: {prompt_len}")

        contains_placeholder = "[state_machine_output]" in prompt if isinstance(prompt, str) else False
        check(f"  Node '{name}' prompt does not contain '[state_machine_output]'",
              not contains_placeholder)

    # ── SUMMARY ──
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    total = passed + failed
    print(f"  Total checks:  {total}")
    print(f"  Passed:        {passed}")
    print(f"  Failed:        {failed}")

    if failures:
        print(f"\n  FAILURES ({len(failures)}):")
        for i, f in enumerate(failures, 1):
            print(f"    {i}. {f}")
    else:
        print("\n  ALL CHECKS PASSED!")

    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
