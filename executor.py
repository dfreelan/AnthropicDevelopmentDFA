"""DFA execution engine - runs Claude CLI calls in background threads."""

import json as json_mod
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime

import git_ops

CLAUDE_BIN = "PLACEHOLDER"
MAX_HFA_DEPTH = 10

MODEL_OPUS = "claude-opus-4-6"
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_HAIKU = "claude-haiku-4-5-20251001"
DEFAULT_MODEL = MODEL_OPUS


class DFAExecutor:
    """Executes a DFA graph by calling claude -p for each state."""

    def __init__(self, graph, input_data=None, image_paths=None, temp_dir=None, include_original_query=False):
        self.execution_id = str(uuid.uuid4())
        self.graph = graph
        self.graph_id = graph.get("id", "")
        self.cwd = graph.get("working_directory") or None
        self.status = "running"
        self.current_node = graph.get("start_node")
        self.history = []
        self.pending_question = None
        self.incoming_data = input_data or "None - this is the start state"
        self._feedback_event = threading.Event()
        self._feedback_response = None
        self._thread = None
        self._stop_event = threading.Event()
        self.live_output = ""  # streaming output from current node
        self.image_paths = image_paths or []
        self.temp_dir = temp_dir
        self.original_query = input_data if include_original_query else None
        self._current_proc = None
        self._stdin_lock = threading.Lock()
        self._keep_alive_active = False
        self.last_session_id = None

    def start(self):
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._feedback_event.set()  # unblock if waiting
        self._keep_alive_active = False
        # Close stdin of current process to unblock it
        proc = self._current_proc
        if proc and proc.stdin:
            try:
                proc.stdin.close()
            except Exception:
                pass
        self.status = "stopped"

    def submit_feedback(self, response):
        self._feedback_response = response
        self._feedback_event.set()

    def get_state(self):
        return {
            "execution_id": self.execution_id,
            "graph_id": self.graph["id"],
            "status": self.status,
            "current_node": self.current_node,
            "history": self.history,
            "pending_question": self.pending_question,
            "incoming_data": self.incoming_data,
            "live_output": self.live_output,
        }

    def send_user_message(self, text):
        """Send a user message to the running Claude process via stdin."""
        with self._stdin_lock:
            proc = self._current_proc
            if proc is None or proc.poll() is not None:
                return False
            try:
                msg = json_mod.dumps({
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": text}]
                    }
                }) + "\n"
                proc.stdin.write(msg)
                proc.stdin.flush()
                return True
            except (OSError, BrokenPipeError, ValueError):
                return False

    def _run_claude(self, prompt, timeout=900, keep_alive=False, model=None):
        """Run claude CLI with streaming JSON output for live updates.

        Returns a tuple (response_text, session_id) so callers can resume
        the session if the response is incomplete.
        """
        self.live_output = ""
        result_text = ""
        session_id = None
        # Append system prompt to nudge Claude to finish work before ending turn.
        # This helps prevent premature end_turn when subagents (Task tool) are pending.
        APPEND_SYSTEM = (
            "CRITICAL REQUIREMENT: You MUST end your response with a <state_machine_output> XML block. "
            "If you launched subagents using the Task tool, you MUST call TaskOutput to collect their "
            "results BEFORE producing your final response. Never say 'I will wait' — actually call "
            "TaskOutput with block: true to wait. Do not end your turn while tasks are pending."
        )
        cmd = [CLAUDE_BIN, "--dangerously-skip-permissions",
               "--output-format", "stream-json",
               "--input-format", "stream-json",
               "--verbose", "-p",
               "--append-system-prompt", APPEND_SYSTEM]
        if model:
            cmd.extend(["--model", model])
        self.live_output = f"[Starting Claude CLI in {self.cwd or 'default dir'}...]\n"
        try:
            env = os.environ.copy()
            env.pop("CLAUDECODE", None)
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "128000"
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=self.cwd,
                env=env,
            )
            self._current_proc = proc
            # Send prompt as NDJSON message for stream-json input format
            msg = json_mod.dumps({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}]
                }
            })
            proc.stdin.write(msg + "\n")
            proc.stdin.flush()
        except Exception as e:
            self.live_output += f"[FAILED to start process: {e}]\n"
            return f"ERROR: Failed to start Claude CLI: {e}", None
        self.live_output += f"[Process started, PID {proc.pid}. Reading output...]\n"

        # Drain stderr in background to prevent pipe deadlock
        stderr_lines = []
        def _drain_stderr():
            try:
                for line in proc.stderr:
                    stderr_lines.append(line.rstrip())
            except Exception:
                pass
        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        # Monitor stop event in background to kill process and unblock readline
        def _stop_watcher():
            self._stop_event.wait()
            try:
                proc.kill()
            except Exception:
                pass
        stop_thread = threading.Thread(target=_stop_watcher, daemon=True)
        stop_thread.start()

        last_activity = time.time()
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                last_activity = time.time()
                if self._stop_event.is_set():
                    proc.kill()
                    proc.wait()
                    return result_text or self.live_output, session_id
                if time.time() - last_activity > timeout:
                    proc.kill()
                    proc.wait()
                    return (result_text or self.live_output) + \
                        f"\n\n[TIMEOUT: no output for {timeout}s]", session_id
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json_mod.loads(line)
                except json_mod.JSONDecodeError:
                    self.live_output += line + "\n"
                    continue
                etype = event.get("type", "")
                if etype == "system":
                    session_id = event.get("session_id")
                    self.live_output += f"[Claude CLI initialized: model={event.get('model','?')}, session={session_id}]\n"
                elif etype == "assistant":
                    msg = event.get("message", {})
                    for block in msg.get("content", []):
                        if block.get("type") == "text":
                            txt = block["text"]
                            self.live_output += "\n--- Claude ---\n" + txt + "\n"
                            result_text += txt
                        elif block.get("type") == "tool_use":
                            tool_name = block.get("name", "?")
                            tool_input = block.get("input", {})
                            summary = str(tool_input)
                            if len(summary) > 200:
                                summary = summary[:200] + "..."
                            self.live_output += f"\n[tool: {tool_name}] {summary}\n"
                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        self.live_output += delta.get("text", "")
                elif etype == "content_block_start":
                    cb = event.get("content_block", {})
                    if cb.get("type") == "tool_use":
                        self.live_output += f"\n[tool: {cb.get('name', '?')}] "
                elif etype == "result":
                    result_subtype = event.get("subtype", "unknown")
                    result_text = event.get("result", result_text)
                    if result_subtype != "success":
                        self.live_output += f"\n[Result subtype: {result_subtype}]\n"
                    break  # result received; exit read loop (process may stay alive)
            if keep_alive and proc.poll() is None:
                # Keep the process alive for interactive chat
                self._keep_alive_active = True
                reader = threading.Thread(target=self._interactive_reader, args=(proc,), daemon=True)
                reader.start()
                return result_text or self.live_output, session_id
            proc.kill()
            proc.wait()
            stderr_thread.join(timeout=5)
            if stderr_lines and (proc.returncode != 0 or not result_text):
                self.live_output += f"\n[stderr: {chr(10).join(stderr_lines[-10:])}]\n"
            if proc.returncode != 0 and not result_text:
                return self.live_output or f"CLI exited with code {proc.returncode}", session_id
            return result_text or self.live_output, session_id
        except Exception as e:
            proc.kill()
            proc.wait()
            return (result_text or self.live_output or "") + f"\n\nERROR: {e}", session_id
        finally:
            if not self._keep_alive_active:
                self._current_proc = None
                if not proc.stdin.closed:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
            self._keep_alive_active = False

    def _interactive_reader(self, proc):
        """Background thread to keep reading from a kept-alive Claude process."""
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                if self._stop_event.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json_mod.loads(line)
                except json_mod.JSONDecodeError:
                    self.live_output += line + "\n"
                    continue
                etype = event.get("type", "")
                if etype == "assistant":
                    msg = event.get("message", {})
                    for block in msg.get("content", []):
                        if block.get("type") == "text":
                            self.live_output += "\n--- Claude ---\n" + block["text"] + "\n"
                        elif block.get("type") == "tool_use":
                            tool_name = block.get("name", "?")
                            tool_input = str(block.get("input", {}))
                            if len(tool_input) > 200:
                                tool_input = tool_input[:200] + "..."
                            self.live_output += f"\n[tool: {tool_name}] {tool_input}\n"
                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        self.live_output += delta.get("text", "")
                elif etype == "content_block_start":
                    cb = event.get("content_block", {})
                    if cb.get("type") == "tool_use":
                        self.live_output += f"\n[tool: {cb.get('name', '?')}] "
                elif etype == "result":
                    result_text = event.get("result", "")
                    if result_text:
                        self.live_output += "\n--- Claude ---\n" + result_text + "\n"
                    # Don't break - wait for next user message to trigger new events
        except Exception:
            pass
        finally:
            # Process died or stream ended
            if self.status == "interactive":
                self.status = "completed"
            self._current_proc = None
            try:
                proc.kill()
                proc.wait()
            except Exception:
                pass

    def end_interactive(self):
        """End the interactive chat session and transition to completed."""
        proc = self._current_proc
        if proc:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.kill()
                proc.wait()
            except Exception:
                pass
            self._current_proc = None
        self.status = "completed"

    def _resume_claude(self, session_id, nudge_prompt, timeout=900, model=None):
        """Resume an existing Claude CLI session with a nudge prompt.

        Used when a prior run ended prematurely (e.g. Claude stopped before
        producing the state_machine_output block). Resumes the same session
        so Claude retains context of its prior work.

        Returns a tuple (response_text, session_id).
        """
        self.live_output = ""
        result_text = ""
        new_session_id = None
        cmd = [CLAUDE_BIN, "--dangerously-skip-permissions",
               "--output-format", "stream-json",
               "--verbose", "-p",
               "--resume", session_id,
               nudge_prompt]
        if model:
            cmd.extend(["--model", model])
        self.live_output = f"[Resuming session {session_id}...]\n"
        try:
            env = os.environ.copy()
            env.pop("CLAUDECODE", None)
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "128000"
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=self.cwd,
                env=env,
            )
            self._current_proc = proc
            # Close stdin immediately since we passed prompt as CLI argument
            proc.stdin.close()
        except Exception as e:
            self.live_output += f"[FAILED to resume: {e}]\n"
            return f"ERROR: Failed to resume Claude CLI: {e}", None

        # Drain stderr in background to prevent pipe deadlock
        stderr_lines = []
        def _drain_stderr():
            try:
                for line in proc.stderr:
                    stderr_lines.append(line.rstrip())
            except Exception:
                pass
        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        # Monitor stop event in background
        def _stop_watcher():
            self._stop_event.wait()
            try:
                proc.kill()
            except Exception:
                pass
        stop_thread = threading.Thread(target=_stop_watcher, daemon=True)
        stop_thread.start()

        last_activity = time.time()
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                last_activity = time.time()
                if self._stop_event.is_set():
                    proc.kill()
                    proc.wait()
                    return result_text or self.live_output, new_session_id
                if time.time() - last_activity > timeout:
                    proc.kill()
                    proc.wait()
                    return (result_text or self.live_output) + f"\n\n[TIMEOUT: no output for {timeout}s]", new_session_id
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json_mod.loads(line)
                except json_mod.JSONDecodeError:
                    self.live_output += line + "\n"
                    continue
                etype = event.get("type", "")
                if etype == "system":
                    new_session_id = event.get("session_id", session_id)
                    self.live_output += f"[Session resumed: {new_session_id}]\n"
                elif etype == "assistant":
                    msg = event.get("message", {})
                    for block in msg.get("content", []):
                        if block.get("type") == "text":
                            txt = block["text"]
                            self.live_output += "\n--- Claude (resumed) ---\n" + txt + "\n"
                            result_text += txt
                        elif block.get("type") == "tool_use":
                            tool_name = block.get("name", "?")
                            tool_input = block.get("input", {})
                            summary = str(tool_input)
                            if len(summary) > 200:
                                summary = summary[:200] + "..."
                            self.live_output += f"\n[tool: {tool_name}] {summary}\n"
                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        self.live_output += delta.get("text", "")
                elif etype == "content_block_start":
                    cb = event.get("content_block", {})
                    if cb.get("type") == "tool_use":
                        self.live_output += f"\n[tool: {cb.get('name', '?')}] "
                elif etype == "result":
                    result_text = event.get("result", result_text)
                    break
            proc.kill()
            proc.wait()
            stderr_thread.join(timeout=5)
            if proc.returncode != 0 and not result_text:
                return self.live_output or f"CLI exited with code {proc.returncode}", new_session_id
            return result_text or self.live_output, new_session_id
        except Exception as e:
            proc.kill()
            proc.wait()
            return (result_text or self.live_output or "") + f"\n\nERROR: {e}", new_session_id
        finally:
            self._current_proc = None

    @staticmethod
    def _sanitize_for_prompt(text):
        """Escape <state_machine_output> tags in injected content.

        Prevents embedded tags from prior nodes from:
        1. Confusing Claude into not producing its own output block
        2. Acting as false regex matches in _parse_response
        """
        if not text:
            return text
        return (text
                .replace("<state_machine_output>", "__PRIOR_STATE_MACHINE_OUTPUT_REDACTED__")
                .replace("</state_machine_output>", "__END_PRIOR_STATE_MACHINE_OUTPUT_REDACTED__"))

    def _resolve_model(self, node):
        """Determine which model to use for a node.

        Checks the graph-level override first; if set to a specific model,
        that wins. Otherwise falls back to the node's individual setting.
        """
        override = self.graph.get("model_override", "individual")
        if override != "individual":
            return override  # override IS the model ID itself
        resolved = node.get("model", DEFAULT_MODEL)
        print(f"[model] Node '{node.get('name','?')}': resolved={resolved} (node.model={node.get('model','MISSING')}, override={override})")
        return resolved

    @staticmethod
    def _build_incoming_data(raw_response, data):
        """Build incoming data for the next node from both response and data tag.

        Includes the previous node's full response so the next node gets
        complete context, not just what the LLM chose to put in <data>.
        """
        # Strip the state_machine_output block itself from the response
        # to avoid redundancy with the data section
        cleaned = re.sub(
            r"(?:<state_machine_output>|\[state_machine_output\]).*?(?:</state_machine_output>|\[/state_machine_output\])",
            "", raw_response, flags=re.DOTALL
        ).strip()

        parts = []
        if cleaned:
            parts.append(f"Previous node response:\n{cleaned}")
        if data:
            parts.append(f"Data passed:\n{data}")
        return "\n\n".join(parts) if parts else data or ""

    def _build_prompt(self, node, context_nodes=None, injected_transitions=None, hfa_incoming=None):
        transitions = node.get("transitions", [])
        if injected_transitions is not None:
            transitions = injected_transitions
        nodes = context_nodes or self.graph.get("nodes", {})

        transition_lines = []
        for t in transitions:
            target_name = t.get("target_state", "unknown")
            for nid, n in nodes.items():
                if n.get("name") == target_name or nid == target_name:
                    target_name = n.get("name", target_name)
                    break
            transition_lines.append(
                f'- {t["label"]}: proceeds to "{target_name}" node'
            )

        transitions_text = "\n".join(transition_lines) if transition_lines else "- (no transitions defined - this is a terminal node)"

        if injected_transitions is not None:
            transitions_text += "\n\nIMPORTANT: You MUST choose one of the named transitions listed above. Do NOT use '__end__' as a transition — pick the transition that best matches the outcome of your work."

        # Build persistent context block if enabled for this node
        persistent_block = ""
        if node.get("persistent_context"):
            prior_visits = []
            for entry in self.history:
                if entry.get("node_id") == node["id"] and entry.get("raw_response"):
                    prior_visits.append(self._sanitize_for_prompt(entry["raw_response"]))
            if prior_visits:
                visit_xml = "\n".join(
                    f'  <visit number="{i+1}">\n{resp}\n  </visit>'
                    for i, resp in enumerate(prior_visits)
                )
                persistent_block = f"\n\n<persistent_node_context>\n{visit_xml}\n</persistent_node_context>"

        # Build attached images block if images were provided
        images_block = ""
        if self.image_paths:
            image_list = "\n".join(f"  - {p}" for p in self.image_paths)
            images_block = f"""

<attached_images>
The user has attached the following image files. Use the Read tool to view them when relevant:
{image_list}
</attached_images>"""

        # Build original user query block if enabled
        # Sanitize to prevent embedded <state_machine_output> tags from
        # confusing Claude or the response parser
        original_query_block = ""
        if self.original_query is not None:
            original_query_block = f"\n\n<original_user_query>\n{self._sanitize_for_prompt(self.original_query)}\n</original_user_query>"

        hfa_incoming_block = ""
        if hfa_incoming:
            hfa_incoming_block = f"\n\n<hfa_input_data>\nThe following is the input data that was passed to the parent HFA node. Use it as context for your task:\n{self._sanitize_for_prompt(hfa_incoming)}\n</hfa_input_data>"

        sanitized_incoming = self._sanitize_for_prompt(self.incoming_data or "")

        prompt = f"""{node['prompt']}{persistent_block}{images_block}{original_query_block}{hfa_incoming_block}

---
You are a node in a state machine. After completing the task above, you MUST end your response with a structured output block in exactly this format:

<state_machine_output>
  <transition>TRANSITION_LABEL</transition>
  <data>
    YOUR XML DATA HERE
  </data>
</state_machine_output>

Available transitions (choose exactly one):
{transitions_text}
- __human_feedback__: use ONLY if the task is genuinely ambiguous and you need clarification from the human operator. Put your question in the <data> tag.

IMPORTANT: The next state has NO memory of this conversation — it can read files but the ONLY context it receives is what you put in your <data> tag. Include a useful summary of your work, findings, and any information the next node needs to continue. Do not just pass the original query. Make your report as simple as possible, but no simpler.

CRITICAL: Do YOUR node's job, then TRANSITION. Do NOT do the next node's job. If you're a tester, report bugs — don't fix them. If you're a planner, make the plan — don't implement it. You will be revisited with full context. Trust the workflow: transition to let the right node handle the next step.

Data from the previous state:
<previous_state_data>
{sanitized_incoming}
</previous_state_data>"""
        return prompt

    @staticmethod
    def _normalize_response(text):
        """Normalize common Claude output variants to canonical format before parsing.

        Claude sometimes mimics sanitized bracket format or uses wrong inner tag names.
        """
        if not text:
            return text
        # Fix bracket variants of the outer tag
        text = text.replace("[state_machine_output]", "<state_machine_output>")
        text = text.replace("[/state_machine_output]", "</state_machine_output>")
        # Fix common inner tag aliases
        text = re.sub(r'<transition_to>(.*?)</transition_to>', r'<transition>\1</transition>', text, flags=re.DOTALL)
        text = re.sub(r'<summary>(.*?)</summary>', r'<data>\1</data>', text, flags=re.DOTALL)
        return text

    def _parse_response(self, raw_response):
        # Use the LAST match in the response, since Claude's actual output block
        # is always at the end. Earlier matches may be quoted/echoed content from
        # <original_user_query> or <previous_state_data> that contain
        # <state_machine_output> blocks from prior nodes.

        raw_response = self._normalize_response(raw_response)

        # Try strict pattern first — find ALL matches, use the last one
        pattern = r"<state_machine_output>\s*<transition>\s*(.*?)\s*</transition>\s*<data>(.*?)</data>\s*</state_machine_output>"
        matches = list(re.finditer(pattern, raw_response, re.DOTALL))
        if matches:
            match = matches[-1]
            return match.group(1).strip(), match.group(2).strip()
        # Lenient: data tag may be empty or self-closing or missing
        pattern_no_data = r"<state_machine_output>\s*<transition>\s*(.*?)\s*</transition>\s*(?:<data>\s*</data>|<data\s*/>)?\s*</state_machine_output>"
        matches = list(re.finditer(pattern_no_data, raw_response, re.DOTALL))
        if matches:
            return matches[-1].group(1).strip(), ""
        # Last resort: search live_output (full accumulated text)
        if self.live_output:
            matches = list(re.finditer(pattern, self._normalize_response(self.live_output), re.DOTALL))
            if matches:
                match = matches[-1]
                return match.group(1).strip(), match.group(2).strip()
        return None, None

    def _find_node_by_id(self, node_id):
        nodes = self.graph.get("nodes", {})
        return nodes.get(node_id)

    def _find_node_by_name(self, name):
        nodes = self.graph.get("nodes", {})
        for nid, node in nodes.items():
            if node.get("name") == name:
                return node
        return None

    def _resolve_transition(self, node, transition_label):
        for t in node.get("transitions", []):
            if t["label"] == transition_label:
                target = self._find_node_by_name(t["target_state"])
                if not target:
                    target = self._find_node_by_id(t["target_state"])
                return t, target
        return None, None

    def _find_node_in_dict(self, name_or_id, nodes_dict):
        """Find a node by name or ID in a specific nodes dict."""
        if name_or_id in nodes_dict:
            return nodes_dict[name_or_id]
        for node in nodes_dict.values():
            if node.get("name") == name_or_id:
                return node
        return None

    def _resolve_transition_in(self, node, transition_label, nodes_dict):
        """Resolve a transition within a specific nodes dict."""
        for t in node.get("transitions", []):
            if t["label"] == transition_label:
                target = self._find_node_in_dict(t["target_state"], nodes_dict)
                return t, target
        return None, None

    def _run_loop(self):
        try:
            # Pre-execution git snapshot (disabled)
            # if self.cwd and git_ops.is_git_repo(self.cwd):
            #     if git_ops.has_changes(self.cwd):
            #         msg = git_ops.generate_commit_message(self.cwd, context_hint="Pre-execution snapshot")
            #         sha = git_ops.auto_commit(self.cwd, msg)
            #         if sha:
            #             git_ops.add_snapshot(self.graph_id, sha, f"Pre-execution: {msg}")
            #     else:
            #         # No changes but seed timeline with current HEAD if timeline is empty
            #         timeline = git_ops.load_timeline(self.graph_id)
            #         if not timeline["snapshots"]:
            #             sha = git_ops.get_current_sha(self.cwd)
            #             if sha:
            #                 git_ops.add_snapshot(self.graph_id, sha, "Initial state")

            # HFA execution stack: each frame = {nodes_dict, hfa_node, parent_nodes_dict}
            exec_stack = []
            active_nodes = self.graph.get("nodes", {})

            while not self._stop_event.is_set():
                node = self._find_node_in_dict(self.current_node, active_nodes)
                if not node:
                    self.status = "error"
                    self.history.append({
                        "node_id": self.current_node,
                        "node_name": "unknown",
                        "error": f"Node not found: {self.current_node}",
                        "timestamp": datetime.now().isoformat(),
                    })
                    return

                # If this is an HFA node, descend into its child graph
                if node.get("is_hfa") and node.get("child_graph", {}).get("nodes"):
                    child_graph = node["child_graph"]
                    child_start = child_graph.get("start_node")
                    if child_start:
                        if len(exec_stack) >= MAX_HFA_DEPTH:
                            self.status = "error"
                            self.history.append({
                                "node_id": node["id"],
                                "node_name": node.get("name", ""),
                                "error": f"Max HFA nesting depth ({MAX_HFA_DEPTH}) exceeded",
                                "timestamp": datetime.now().isoformat(),
                            })
                            return
                        exec_stack.append({
                            "nodes_dict": active_nodes,
                            "hfa_node": node,
                            "hfa_incoming": self.incoming_data if node.get("forward_incoming_to_children") else None,
                        })
                        active_nodes = child_graph["nodes"]
                        self.current_node = child_start
                        continue

                # Determine if we need to inject parent transitions
                # (when we're in a sub-graph and at a terminal child node)
                use_transitions = None
                prompt_context_nodes = active_nodes
                if exec_stack and not node.get("transitions"):
                    parent_frame = exec_stack[-1]
                    parent_hfa = parent_frame["hfa_node"]
                    if parent_hfa.get("transitions"):
                        use_transitions = parent_hfa["transitions"]
                        prompt_context_nodes = parent_frame["nodes_dict"]

                hfa_incoming = None
                if exec_stack:
                    hfa_incoming = exec_stack[-1].get("hfa_incoming")

                prompt = self._build_prompt(
                    node,
                    context_nodes=prompt_context_nodes,
                    injected_transitions=use_transitions,
                    hfa_incoming=hfa_incoming,
                )

                node_model = self._resolve_model(node)
                max_attempts = 3
                session_id = None
                for attempt in range(max_attempts):
                    try:
                        if attempt == 0 or session_id is None:
                            # First attempt or no session to resume: fresh run
                            raw_response, session_id = self._run_claude(prompt, model=node_model)
                        else:
                            # Retry by resuming the existing session with a nudge
                            # so Claude can pick up where it left off rather than
                            # starting from scratch
                            if "exceeded the" in raw_response and "output token maximum" in raw_response:
                                nudge = (
                                    "Your previous response was cut off because it exceeded the output token limit. "
                                    "You MUST be drastically more concise this time. Produce ONLY the required "
                                    "<state_machine_output> XML block with the transition and essential data. "
                                    "Omit all explanation, commentary, and verbose output."
                                )
                            else:
                                nudge = (
                                    "Your previous response ended prematurely without producing the required "
                                    "state_machine_output XML block. If you have subagents/tasks still running, "
                                    "collect their results now using TaskOutput. Then produce your final answer "
                                    "with the <state_machine_output> block as instructed."
                                )
                            raw_response, session_id = self._resume_claude(session_id, nudge, model=node_model)
                    except Exception as e:
                        raw_response = f"ERROR: {e}"

                    if self._stop_event.is_set():
                        return

                    transition_label, data = self._parse_response(raw_response)

                    if transition_label is not None:
                        break

                    if attempt < max_attempts - 1:
                        self.history.append({
                            "node_id": node["id"],
                            "node_name": node.get("name", ""),
                            "prompt_sent": prompt,
                            "raw_response": raw_response,
                            "transition_taken": None,
                            "data_passed": None,
                            "error": f"Parse failed (attempt {attempt + 1}/{max_attempts}), retrying via session resume...",
                            "timestamp": datetime.now().isoformat(),
                        })
                        # Exponential backoff: 2s, 4s, capped at 20s
                        delay = min(2 ** (attempt + 1), 20)
                        self.live_output += f"[Retrying in {delay}s...]\n"
                        if self._stop_event.wait(timeout=delay):
                            return  # User hit Stop during backoff

                if transition_label is None:
                    self.history.append({
                        "node_id": node["id"],
                        "node_name": node.get("name", ""),
                        "prompt_sent": prompt,
                        "raw_response": raw_response,
                        "transition_taken": None,
                        "data_passed": None,
                        "error": "No valid <state_machine_output> block found in response after 3 attempts",
                        "timestamp": datetime.now().isoformat(),
                    })
                    self.status = "error"
                    return

                # Handle human feedback
                if transition_label == "__human_feedback__":
                    self.pending_question = data
                    self.status = "paused_for_feedback"
                    self.history.append({
                        "node_id": node["id"],
                        "node_name": node.get("name", ""),
                        "prompt_sent": prompt,
                        "raw_response": raw_response,
                        "transition_taken": "__human_feedback__",
                        "data_passed": data,
                        "timestamp": datetime.now().isoformat(),
                    })

                    self._feedback_event.clear()
                    self._feedback_event.wait()

                    if self._stop_event.is_set():
                        return

                    self.incoming_data = (
                        self.incoming_data
                        + f"\n<human_feedback_response>{self._feedback_response}</human_feedback_response>"
                    )
                    self.pending_question = None
                    self.status = "running"
                    self._feedback_response = None
                    continue

                # Try to resolve the transition in the current active_nodes
                transition_obj, target_node = self._resolve_transition_in(
                    node, transition_label, active_nodes
                )

                # If not found locally and we injected parent transitions,
                # pop back up to the parent scope and resolve there
                if not transition_obj and use_transitions and exec_stack:
                    parent_frame = exec_stack.pop()
                    parent_hfa = parent_frame["hfa_node"]
                    parent_nodes = parent_frame["nodes_dict"]

                    # Record history for the child node
                    self.history.append({
                        "node_id": node["id"],
                        "node_name": node.get("name", ""),
                        "prompt_sent": prompt,
                        "raw_response": raw_response,
                        "transition_taken": transition_label,
                        "data_passed": data,
                        "timestamp": datetime.now().isoformat(),
                    })

                    # Resolve in parent's nodes using the HFA node's transitions
                    transition_obj, target_node = self._resolve_transition_in(
                        parent_hfa, transition_label, parent_nodes
                    )
                    active_nodes = parent_nodes

                    if not transition_obj or not target_node:
                        # __end__ from a child terminal node means "child graph complete"
                        # Auto-follow the first parent transition as a graceful fallback
                        if transition_label.strip() == "__end__":
                            parent_transitions = parent_hfa.get("transitions", [])
                            if parent_transitions:
                                fallback_t = parent_transitions[0]
                                fallback_target = self._find_node_in_dict(
                                    fallback_t["target_state"], parent_nodes
                                )
                                if fallback_target:
                                    self.history.append({
                                        "node_id": parent_hfa["id"],
                                        "node_name": parent_hfa.get("name", ""),
                                        "transition_taken": fallback_t["label"],
                                        "data_passed": data,
                                        "note": f"Auto-resolved __end__ from child to parent transition '{fallback_t['label']}'",
                                        "timestamp": datetime.now().isoformat(),
                                    })
                                    self.incoming_data = self._build_incoming_data(raw_response, data)
                                    self.current_node = fallback_target["id"]
                                    continue
                            # No parent transitions — parent is also terminal, mark complete
                            self.status = "completed"
                            self.incoming_data = self._build_incoming_data(raw_response, data)
                            return

                        self.history.append({
                            "node_id": parent_hfa["id"],
                            "node_name": parent_hfa.get("name", ""),
                            "transition_taken": transition_label,
                            "data_passed": data,
                            "error": f"Invalid transition label in parent: {transition_label}",
                            "timestamp": datetime.now().isoformat(),
                        })
                        self.status = "error"
                        return

                    # Move to the parent-level target
                    # Include both the node's full response and its explicit data
                    self.incoming_data = self._build_incoming_data(raw_response, data)
                    self.current_node = target_node["id"]

                    # Check if this target is terminal at the parent level
                    if not target_node.get("transitions"):
                        # Check if it's an HFA (will be handled next iteration)
                        if not target_node.get("is_hfa"):
                            terminal_model = self._resolve_model(target_node)
                            terminal_prompt = self._build_prompt(target_node, context_nodes=active_nodes)
                            try:
                                terminal_response, session_id = self._run_claude(terminal_prompt, keep_alive=True, model=terminal_model)
                            except Exception:
                                terminal_response = "(terminal node execution failed)"
                                session_id = None
                            self.history.append({
                                "node_id": target_node["id"],
                                "node_name": target_node.get("name", ""),
                                "prompt_sent": terminal_prompt,
                                "raw_response": terminal_response,
                                "transition_taken": None,
                                "data_passed": None,
                                "timestamp": datetime.now().isoformat(),
                            })
                            if self._current_proc is not None and self._current_proc.poll() is None:
                                self.status = "interactive"
                                self.last_session_id = session_id
                                self.live_output += "\n\n=== DFA Complete - Interactive Chat Mode ===\nYou can now chat with Claude directly. Send messages below.\n\n"
                            else:
                                self.status = "completed"
                            return
                    continue

                if not transition_obj:
                    self.history.append({
                        "node_id": node["id"],
                        "node_name": node.get("name", ""),
                        "prompt_sent": prompt,
                        "raw_response": raw_response,
                        "transition_taken": transition_label,
                        "data_passed": data,
                        "error": f"Invalid transition label: {transition_label}",
                        "timestamp": datetime.now().isoformat(),
                    })
                    self.status = "error"
                    return

                # Record history
                self.history.append({
                    "node_id": node["id"],
                    "node_name": node.get("name", ""),
                    "prompt_sent": prompt,
                    "raw_response": raw_response,
                    "transition_taken": transition_label,
                    "data_passed": data,
                    "timestamp": datetime.now().isoformat(),
                })

                if not target_node:
                    self.history[-1]["error"] = f"Target node not found for transition '{transition_label}'"
                    self.status = "error"
                    return

                # Move to next node — include response context so next node
                # gets both the full output and explicitly passed data
                self.incoming_data = self._build_incoming_data(raw_response, data)
                self.current_node = target_node["id"]

                # If target node has no transitions and isn't an HFA, run as terminal
                if not target_node.get("transitions") and not target_node.get("is_hfa"):
                    # But if we're in a sub-graph, check if we should pop up
                    if exec_stack:
                        # This node will be processed next iteration where
                        # parent transitions will be injected
                        continue

                    terminal_model = self._resolve_model(target_node)
                    terminal_prompt = self._build_prompt(target_node, context_nodes=active_nodes)
                    try:
                        terminal_response, session_id = self._run_claude(terminal_prompt, keep_alive=True, model=terminal_model)
                    except Exception:
                        terminal_response = "(terminal node execution failed)"
                        session_id = None

                    self.history.append({
                        "node_id": target_node["id"],
                        "node_name": target_node.get("name", ""),
                        "prompt_sent": terminal_prompt,
                        "raw_response": terminal_response,
                        "transition_taken": None,
                        "data_passed": None,
                        "timestamp": datetime.now().isoformat(),
                    })
                    if self._current_proc is not None and self._current_proc.poll() is None:
                        self.status = "interactive"
                        self.last_session_id = session_id
                        self.live_output += "\n\n=== DFA Complete - Interactive Chat Mode ===\nYou can now chat with Claude directly. Send messages below.\n\n"
                    else:
                        self.status = "completed"
                    return

        except Exception as e:
            self.status = "error"
            self.history.append({
                "node_id": self.current_node,
                "node_name": "",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            })
        finally:
            # Post-execution git snapshot (disabled)
            # try:
            #     if self.cwd and git_ops.is_git_repo(self.cwd) and git_ops.has_changes(self.cwd):
            #         context = f"After DFA execution of graph '{self.graph.get('name', 'unknown')}'"
            #         msg = git_ops.generate_commit_message(self.cwd, context_hint=context)
            #         sha = git_ops.auto_commit(self.cwd, msg)
            #         if sha:
            #             git_ops.add_snapshot(self.graph_id, sha, f"Post-execution: {msg}")
            # except Exception:
            #     pass  # Never let post-commit logic crash the executor
            # Clean up temp image directory
            if self.temp_dir:
                try:
                    shutil.rmtree(self.temp_dir, ignore_errors=True)
                except Exception:
                    pass


# Global registry of active executions
_executions = {}
_lock = threading.Lock()


def start_execution(graph, input_data=None, image_paths=None, temp_dir=None, include_original_query=False):
    executor = DFAExecutor(graph, input_data, image_paths=image_paths, temp_dir=temp_dir, include_original_query=include_original_query)
    with _lock:
        _executions[executor.execution_id] = executor
    executor.start()
    return executor


def get_execution(exec_id):
    with _lock:
        return _executions.get(exec_id)


def stop_execution(exec_id):
    with _lock:
        executor = _executions.get(exec_id)
    if executor:
        executor.stop()
        return True
    return False


def get_active_execution_for_graph(graph_id):
    """Find a running/paused execution for a given graph_id."""
    with _lock:
        for exec_id, exc in reversed(list(_executions.items())):
            if exc.graph_id == graph_id and exc.status in ("running", "paused_for_feedback", "interactive"):
                return exc
    return None
