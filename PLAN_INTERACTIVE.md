# Plan: Interactive Claude Sessions in DFA Executor

## Status: Step 1 (WebSocket infra) is DONE. Steps 2-7 remain.

## Architecture Overview

Replace the current `claude -p` (fire-and-forget) execution model with bidirectional streaming sessions using `claude -p --input-format stream-json --output-format stream-json`. The web UI gets a WebSocket connection for real-time output streaming and user input.

**Key insight**: Claude CLI's `--input-format stream-json` mode accepts NDJSON on stdin. The message format is:
```json
{"type":"message","role":"user","content":[{"type":"text","text":"Your message here"}]}
```
This lets us send follow-up user messages to a running session. Combined with `--output-format stream-json`, we get structured bidirectional communication while keeping the `-p` (non-interactive) flag.

## Step-by-Step Plan

### Step 1: Add WebSocket support to the Flask backend ✅ DONE

Already implemented:
- Flask app wrapped with SocketIO (`app.py` line 16)
- Event handlers: `connect`, `disconnect`, `subscribe_execution`, `unsubscribe_execution`
- `user_message` handler skeleton (TODO: wire to executor)
- Server runs with `socketio.run()`

### Step 2: Refactor `_run_claude()` to use bidirectional stream-json

**Files**: `executor.py`

Changes to `_run_claude()`:
- Add `--input-format stream-json` to CLI command
- Send initial prompt as NDJSON: `{"type":"message","role":"user","content":[{"type":"text","text":"<full prompt>"}]}\n`
- **Keep stdin OPEN** (don't call `proc.stdin.close()`)
- Store `self._current_proc` and add `self._stdin_lock = threading.Lock()` on executor instance

Add `send_user_message(text)` method:
```python
def send_user_message(self, text):
    with self._stdin_lock:
        if self._current_proc and self._current_proc.stdin and not self._current_proc.stdin.closed:
            msg = json_mod.dumps({"type":"message","role":"user","content":[{"type":"text","text":text}]})
            self._current_proc.stdin.write(msg + "\n")
            self._current_proc.stdin.flush()
```

Add cleanup: close stdin before killing process in stop scenarios.

Output parsing stays the same. `_parse_response()` unchanged.

**Tests**: Run a single node execution, verify Claude receives the prompt and produces output. Verify `state_machine_output` parsing still works.

### Step 3: Wire user message injection from WebSocket to executor

**Files**: `app.py`

In `handle_user_message()` (line 271-284):
- Replace the TODO with: `exc.send_user_message(message)`
- Emit `message_ack` with status `sent` (not just `received`)

**Tests**: Start an execution, send a user message via WebSocket, verify Claude receives it and responds.

### Step 4: Emit live output via WebSocket

**Files**: `executor.py`, `app.py`

In `_run_claude()` output reading loop, emit WebSocket events:
- Need to pass `socketio` instance to executor (via module-level reference or dependency injection)
- Emit to room `execution_id`:
  - `exec_output` — text chunks (from `content_block_delta` events)
  - `exec_tool_use` — tool invocation summaries
  - `exec_status` — status changes
  - `exec_node_complete` — when a node finishes (history entry added)
- Keep `self.live_output` for REST polling fallback

**Tests**: Start execution, verify WebSocket client receives streaming output events.

### Step 5: Build the interactive terminal UI in the frontend

**Files**: `static/app.js`, `templates/index.html`, `static/style.css`

- Add Socket.IO client library (CDN in `index.html`)
- On execution start: connect WebSocket, subscribe to execution
- Replace polling-based output with WebSocket-driven:
  - `exec_output` → append to log
  - `exec_tool_use` → show tool indicators
  - `exec_status` → update status display
  - `exec_node_complete` → update node markers
- Keep polling as fallback for status/history
- Add **input bar** at bottom of execution panel:
  - Always visible during execution (not just during `__human_feedback__`)
  - Enter sends via WebSocket `user_message` event
  - Messages appear in log with "You:" prefix
  - The existing `__human_feedback__` bar becomes a special case with a question prompt

**Tests**: Start execution in web UI, type a message, verify it appears in output and Claude responds.

### Step 6: Auto-detect `state_machine_output` and clean transitions ✅ MOSTLY DONE

Already works: `_parse_response()` detects the output block, `_run_loop()` handles transitions.

Minor additions needed:
- Emit `exec_node_transition` WebSocket event when transitioning
- Close stdin gracefully before killing process on transition
- New node's session becomes the active interactive session

### Step 7: Edge cases and polish

- **Cancellation**: Close stdin before killing subprocess
- **Process cleanup**: Ensure subprocess and stdin are cleaned up when execution ends
- **Reconnection**: On browser reconnect, re-subscribe and fetch current state from REST API
- **HFA traversal**: Verify interactive mode works when descending/ascending HFA sub-graphs
- **Multiple concurrent executions**: Already scoped by execution_id
- **Terminal nodes**: Interactive session runs until Claude finishes (no expected `state_machine_output`)
- **Timeout**: Keep existing timeout logic, also handle stdin-open case
