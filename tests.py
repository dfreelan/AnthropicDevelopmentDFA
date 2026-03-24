"""Tests for Step 4: Add Socket.IO client and interactive input bar to frontend

Tests verify that:
1. templates/index.html has Socket.IO CDN script and interactive bar HTML
2. static/style.css has interactive bar CSS styles
3. static/app.js has Socket.IO init, interactive messaging functions, bar visibility logic
4. All previous backend tests still pass (Steps 1-3)
"""

import json
import threading
import subprocess
import sys
import os
import re
import time
import types
import textwrap
from unittest.mock import MagicMock, patch

# Mock git_ops before importing executor since it imports git_ops at module level
git_ops_mock = types.ModuleType("git_ops")
git_ops_mock.is_git_repo = lambda *a, **kw: False
git_ops_mock.has_changes = lambda *a, **kw: False
git_ops_mock.auto_commit = lambda *a, **kw: None
git_ops_mock.add_snapshot = lambda *a, **kw: None
git_ops_mock.load_timeline = lambda *a, **kw: {"snapshots": []}
git_ops_mock.get_current_sha = lambda *a, **kw: None
git_ops_mock.generate_commit_message = lambda *a, **kw: "test"
sys.modules["git_ops"] = git_ops_mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from executor import DFAExecutor, CLAUDE_BIN
import executor as executor_module

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def make_graph(**overrides):
    """Helper to create a minimal valid graph for DFAExecutor."""
    g = {
        "id": "test-graph-1",
        "name": "Test Graph",
        "start_node": "node1",
        "nodes": {
            "node1": {
                "id": "node1",
                "name": "Start",
                "prompt": "Hello",
                "transitions": [],
            }
        },
    }
    g.update(overrides)
    return g


def read_file(relative_path):
    """Read a file relative to the project root."""
    full_path = os.path.join(BASE_DIR, relative_path)
    with open(full_path, "r") as f:
        return f.read()


# ============================================================
# STEP 4 TESTS: Frontend Socket.IO and Interactive Input Bar
# ============================================================

# --- BASIC TESTS (HTML) ---

print("Test 1: index.html includes Socket.IO CDN script tag")
html = read_file("templates/index.html")
assert "socket.io" in html.lower(), \
    f"index.html should contain a Socket.IO script tag"
# Check it's a proper script src tag
socketio_script_pattern = re.search(r'<script\s+src="[^"]*socket\.io[^"]*"[^>]*>\s*</script>', html, re.IGNORECASE)
assert socketio_script_pattern is not None, \
    f"index.html should have a <script src='...socket.io...'></script> tag"
print("  PASSED")


print("Test 2: Socket.IO script loads BEFORE app.js")
html = read_file("templates/index.html")
socketio_pos = html.lower().find("socket.io")
appjs_pos = html.find("app.js")
assert socketio_pos < appjs_pos, \
    f"Socket.IO script (pos {socketio_pos}) must appear before app.js (pos {appjs_pos}) in the HTML"
print("  PASSED")


print("Test 3: index.html contains interactive bar HTML structure")
html = read_file("templates/index.html")
assert 'id="interactiveBar"' in html, \
    f"index.html should contain an element with id='interactiveBar'"
assert 'id="interactiveInput"' in html, \
    f"index.html should contain an input with id='interactiveInput'"
assert 'id="btnSendInteractive"' in html, \
    f"index.html should contain a button with id='btnSendInteractive'"
print("  PASSED")


print("Test 4: Interactive bar is placed between execLog and feedbackBar")
html = read_file("templates/index.html")
exec_log_pos = html.find('id="execLog"')
interactive_bar_pos = html.find('id="interactiveBar"')
feedback_bar_pos = html.find('id="feedbackBar"')
assert exec_log_pos < interactive_bar_pos < feedback_bar_pos, \
    f"interactiveBar (pos {interactive_bar_pos}) must be between execLog (pos {exec_log_pos}) and feedbackBar (pos {feedback_bar_pos})"
print("  PASSED")


print("Test 5: Interactive bar has correct CSS class")
html = read_file("templates/index.html")
assert 'class="interactive-bar"' in html or "class='interactive-bar'" in html, \
    f"interactiveBar element should have class='interactive-bar'"
print("  PASSED")


# --- BASIC TESTS (CSS) ---

print("Test 6: style.css contains .interactive-bar base styles")
css = read_file("static/style.css")
assert ".interactive-bar" in css, \
    f"style.css should contain .interactive-bar selector"
assert "display: none" in css or "display:none" in css, \
    f"style.css should set .interactive-bar to display:none by default"
print("  PASSED")


print("Test 7: style.css contains .interactive-bar.active with display:flex")
css = read_file("static/style.css")
# Find the .interactive-bar.active rule
active_match = re.search(r'\.interactive-bar\.active\s*\{([^}]*)\}', css)
assert active_match is not None, \
    f"style.css should contain a .interactive-bar.active rule"
active_body = active_match.group(1)
assert "flex" in active_body, \
    f".interactive-bar.active should set display:flex, got: {active_body}"
print("  PASSED")


print("Test 8: style.css contains .interactive-bar input styles")
css = read_file("static/style.css")
assert ".interactive-bar input" in css, \
    f"style.css should style .interactive-bar input"
print("  PASSED")


print("Test 9: style.css contains .interactive-bar button styles")
css = read_file("static/style.css")
assert ".interactive-bar button" in css, \
    f"style.css should style .interactive-bar button"
print("  PASSED")


# --- BASIC TESTS (JavaScript) ---

print("Test 10: app.js initializes Socket.IO with io()")
js = read_file("static/app.js")
# Look for socket = io() or const socket = io() etc.
io_init = re.search(r'(const|let|var)\s+socket\s*=\s*io\s*\(', js)
assert io_init is not None, \
    f"app.js should initialize Socket.IO with something like 'const socket = io()'"
print("  PASSED")


# --- EDGE CASE TESTS ---

print("Test 11: app.js has sendInteractiveMessage function")
js = read_file("static/app.js")
assert "sendInteractiveMessage" in js, \
    f"app.js should contain a sendInteractiveMessage function"
# Verify it's actually defined as a function
send_func = re.search(r'function\s+sendInteractiveMessage\s*\(', js)
assert send_func is not None, \
    f"app.js should define 'function sendInteractiveMessage()'"
print("  PASSED")


print("Test 12: sendInteractiveMessage emits 'user_message' via socket")
js = read_file("static/app.js")
# Find the function body and verify it emits user_message
assert "socket.emit" in js, \
    f"app.js should call socket.emit somewhere"
assert "'user_message'" in js or '"user_message"' in js, \
    f"app.js should emit a 'user_message' event via Socket.IO"
print("  PASSED")


print("Test 13: app.js listens for 'message_ack' event")
js = read_file("static/app.js")
assert "message_ack" in js, \
    f"app.js should listen for 'message_ack' events from the server"
ack_listener = re.search(r"socket\.on\s*\(\s*['\"]message_ack['\"]", js)
assert ack_listener is not None, \
    f"app.js should have socket.on('message_ack', ...) listener"
print("  PASSED")


print("Test 14: app.js handles failed message_ack by updating placeholder")
js = read_file("static/app.js")
assert "failed" in js, \
    f"app.js should handle message_ack 'failed' status"
# Check the placeholder is changed on failure
assert "placeholder" in js.lower(), \
    f"app.js should update the placeholder text on send failure"
print("  PASSED")


print("Test 15: app.js has Enter key handler for interactiveInput")
js = read_file("static/app.js")
# Look for keydown/keypress listener on interactiveInput that checks for Enter
enter_handler = re.search(r'interactiveInput\.addEventListener\s*\(\s*["\']key(down|press|up)["\']', js)
assert enter_handler is not None, \
    f"app.js should attach a keyboard event listener to interactiveInput"
assert "Enter" in js, \
    f"app.js should check for the 'Enter' key in the interactiveInput handler"
print("  PASSED")


print("Test 16: app.js has click handler for btnSendInteractive")
js = read_file("static/app.js")
btn_handler = re.search(r'btnSendInteractive.*addEventListener\s*\(\s*["\']click["\']|' +
                        r'\$\s*\(\s*["\']btnSendInteractive["\']\s*\)\.addEventListener\s*\(\s*["\']click["\']', js)
assert btn_handler is not None, \
    f"app.js should attach a click event listener to btnSendInteractive"
print("  PASSED")


print("Test 17: app.js shows interactive bar when execution status is 'running'")
js = read_file("static/app.js")
# Look for logic that adds 'active' class to interactiveBar when status is running
assert 'interactiveBar.classList.add("active")' in js or "interactiveBar.classList.add('active')" in js, \
    f"app.js should add 'active' class to interactiveBar when execution is running"
print("  PASSED")


print("Test 18: app.js hides interactive bar when execution is not running")
js = read_file("static/app.js")
assert 'interactiveBar.classList.remove("active")' in js or "interactiveBar.classList.remove('active')" in js, \
    f"app.js should remove 'active' class from interactiveBar when execution is not running"
print("  PASSED")


print("Test 19: app.js declares DOM references for interactiveBar and interactiveInput")
js = read_file("static/app.js")
bar_ref = re.search(r'(const|let|var)\s+interactiveBar\s*=', js)
input_ref = re.search(r'(const|let|var)\s+interactiveInput\s*=', js)
assert bar_ref is not None, \
    f"app.js should declare interactiveBar as a DOM reference variable"
assert input_ref is not None, \
    f"app.js should declare interactiveInput as a DOM reference variable"
print("  PASSED")


print("Test 20: app.js subscribes to execution room via Socket.IO")
js = read_file("static/app.js")
assert "subscribe_execution" in js, \
    f"app.js should emit 'subscribe_execution' to join the execution room"
subscribe_emit = re.search(r"socket\.emit\s*\(\s*['\"]subscribe_execution['\"]", js)
assert subscribe_emit is not None, \
    f"app.js should call socket.emit('subscribe_execution', ...) when starting execution"
print("  PASSED")


print("Test 21: app.js unsubscribes from execution room when execution ends")
js = read_file("static/app.js")
assert "unsubscribe_execution" in js, \
    f"app.js should emit 'unsubscribe_execution' when execution finishes"
unsubscribe_emit = re.search(r"socket\.emit\s*\(\s*['\"]unsubscribe_execution['\"]", js)
assert unsubscribe_emit is not None, \
    f"app.js should call socket.emit('unsubscribe_execution', ...) on completion"
print("  PASSED")


print("Test 22: sendInteractiveMessage checks for executionId before sending")
js = read_file("static/app.js")
# Extract the function body
func_match = re.search(r'function\s+sendInteractiveMessage\s*\(\s*\)\s*\{(.*?)\n\s*\}', js, re.DOTALL)
assert func_match is not None, \
    f"Should find sendInteractiveMessage function body"
func_body = func_match.group(1)
assert "executionId" in func_body, \
    f"sendInteractiveMessage should check executionId before sending"
print("  PASSED")


print("Test 23: sendInteractiveMessage clears input after sending")
js = read_file("static/app.js")
func_match = re.search(r'function\s+sendInteractiveMessage\s*\(\s*\)\s*\{(.*?)\n\s*\}', js, re.DOTALL)
assert func_match is not None, \
    f"Should find sendInteractiveMessage function body"
func_body = func_match.group(1)
assert 'interactiveInput.value' in func_body and ('= ""' in func_body or "= ''" in func_body), \
    f"sendInteractiveMessage should clear interactiveInput.value after sending"
print("  PASSED")


print("Test 24: sendInteractiveMessage trims whitespace and skips empty messages")
js = read_file("static/app.js")
func_match = re.search(r'function\s+sendInteractiveMessage\s*\(\s*\)\s*\{(.*?)\n\s*\}', js, re.DOTALL)
func_body = func_match.group(1)
assert ".trim()" in func_body, \
    f"sendInteractiveMessage should trim the input text"
assert "!text" in func_body or "text ===" in func_body or 'text === ""' in func_body or "text.length" in func_body, \
    f"sendInteractiveMessage should guard against empty messages after trimming"
print("  PASSED")


print("Test 25: Interactive bar visibility logic is in pollExecution context")
js = read_file("static/app.js")
# The interactive bar show/hide should be near the feedbackBar show/hide in pollExecution
# Find pollExecution function area
poll_match = re.search(r'function\s+pollExecution\s*\(\s*\)\s*\{', js)
assert poll_match is not None, \
    f"app.js should contain a pollExecution function"
poll_start = poll_match.start()
# Find the next function declaration after pollExecution to bound the search
next_func = re.search(r'\n\s*(?:async\s+)?function\s+\w+\s*\(', js[poll_start + 30:])
if next_func:
    poll_body = js[poll_start:poll_start + 30 + next_func.start()]
else:
    poll_body = js[poll_start:]
# The interactive bar logic should be in or after pollExecution
assert "interactiveBar" in poll_body, \
    f"interactiveBar visibility logic should be within or near pollExecution function"
print("  PASSED")


# --- EDGE CASE TESTS (Structure and Ordering) ---

print("Test 26: Interactive bar HTML has input with placeholder text")
html = read_file("templates/index.html")
placeholder_match = re.search(r'id="interactiveInput"[^>]*placeholder="([^"]*)"', html)
assert placeholder_match is not None, \
    f"interactiveInput should have a placeholder attribute"
placeholder = placeholder_match.group(1)
assert len(placeholder) > 0, \
    f"interactiveInput placeholder should not be empty"
print("  PASSED")


print("Test 27: Interactive bar HTML has a label/span for context")
html = read_file("templates/index.html")
# Find the interactiveBar div and check it has a span with descriptive text
bar_match = re.search(r'id="interactiveBar"[^>]*>(.*?)</div>', html, re.DOTALL)
assert bar_match is not None, \
    f"Should find interactiveBar content"
bar_content = bar_match.group(1)
assert "<span" in bar_content, \
    f"interactiveBar should contain a <span> label element"
print("  PASSED")


print("Test 28: CSS interactive-bar has background color defined")
css = read_file("static/style.css")
# Find the .interactive-bar rule and check for background
bar_rule = re.search(r'\.interactive-bar\s*\{([^}]*)\}', css)
assert bar_rule is not None, \
    f"Should find .interactive-bar CSS rule"
bar_body = bar_rule.group(1)
assert "background" in bar_body, \
    f".interactive-bar should have a background property set"
print("  PASSED")


print("Test 29: CSS interactive-bar button has hover state")
css = read_file("static/style.css")
hover_match = re.search(r'\.interactive-bar\s+button:hover\s*\{', css)
assert hover_match is not None, \
    f"style.css should have .interactive-bar button:hover rule"
print("  PASSED")


print("Test 30: socket.emit user_message includes execution_id in payload")
js = read_file("static/app.js")
# Find the socket.emit('user_message', ...) call and verify it includes execution_id
emit_match = re.search(r"socket\.emit\s*\(\s*['\"]user_message['\"],\s*\{([^}]*)\}", js)
assert emit_match is not None, \
    f"Should find socket.emit('user_message', {{...}}) call"
emit_payload = emit_match.group(1)
assert "execution_id" in emit_payload, \
    f"user_message emit should include execution_id in payload, got: {emit_payload}"
assert "message" in emit_payload or "text" in emit_payload, \
    f"user_message emit should include message/text in payload, got: {emit_payload}"
print("  PASSED")


# --- BACKEND REGRESSION TESTS (Steps 1-3 must still pass) ---

original_popen_real = subprocess.Popen
old_bin = executor_module.CLAUDE_BIN


print("Test 31: _run_claude sets _current_proc during execution (regression)")
executor = DFAExecutor(make_graph())

script = textwrap.dedent("""\
    import sys, json, time
    prompt = sys.stdin.readline()
    time.sleep(0.3)
    result = {"type": "result", "result": "test output"}
    sys.stdout.write(json.dumps(result) + "\\n")
    sys.stdout.flush()
""")

old_popen = subprocess.Popen
def patched_popen(cmd, **kwargs):
    new_cmd = [sys.executable, "-c", script]
    kwargs.pop("cwd", None)
    return old_popen(new_cmd, **kwargs)

executor_module.CLAUDE_BIN = sys.executable
subprocess.Popen = patched_popen
try:
    result_holder = [None]
    def run_claude():
        result_holder[0] = executor._run_claude("test prompt")
    t = threading.Thread(target=run_claude)
    t.start()
    time.sleep(0.15)
    assert executor._current_proc is not None, \
        f"_current_proc should be set during execution, got None"
    t.join(timeout=5)
    assert executor._current_proc is None, \
        f"_current_proc should be None after execution, got {executor._current_proc}"
    assert result_holder[0] == ("test output", None), \
        f"Expected ('test output', None), got {result_holder[0]!r}"
finally:
    subprocess.Popen = old_popen
    executor_module.CLAUDE_BIN = old_bin
print("  PASSED")


print("Test 32: _run_claude returns result text from stream-json 'result' event (regression)")
executor = DFAExecutor(make_graph())
script = textwrap.dedent("""\
    import sys, json
    prompt = sys.stdin.readline()
    events = [
        {"type": "system", "model": "test"},
        {"type": "result", "result": "Hello from Claude"}
    ]
    for e in events:
        sys.stdout.write(json.dumps(e) + "\\n")
        sys.stdout.flush()
""")
def patched_popen3(cmd, **kwargs):
    new_cmd = [sys.executable, "-c", script]
    kwargs.pop("cwd", None)
    return old_popen(new_cmd, **kwargs)
subprocess.Popen = patched_popen3
try:
    result = executor._run_claude("test prompt")
    assert result == ("Hello from Claude", None), f"Expected ('Hello from Claude', None), got {result!r}"
finally:
    subprocess.Popen = old_popen
    executor_module.CLAUDE_BIN = old_bin
print("  PASSED")


print("Test 33: send_user_message returns False when _current_proc is None (regression)")
executor = DFAExecutor(make_graph())
result = executor.send_user_message("hello")
assert result is False, f"send_user_message should return False when no process running, got {result}"
print("  PASSED")


print("Test 34: _sanitize_for_prompt still works correctly (regression)")
assert DFAExecutor._sanitize_for_prompt("<state_machine_output>test</state_machine_output>") == \
    "__PRIOR_STATE_MACHINE_OUTPUT_REDACTED__test__END_PRIOR_STATE_MACHINE_OUTPUT_REDACTED__", "Sanitization should replace XML tags with redaction sentinels"
assert DFAExecutor._sanitize_for_prompt(None) is None, "Sanitization of None should return None"
assert DFAExecutor._sanitize_for_prompt("") == "", "Sanitization of empty string should return empty"
print("  PASSED")


print("Test 35: _parse_response still works correctly (regression)")
executor = DFAExecutor(make_graph())
raw = """Some text
<state_machine_output>
  <transition>next</transition>
  <data>some data here</data>
</state_machine_output>"""
transition, data = executor._parse_response(raw)
assert transition == "next", f"Expected transition 'next', got {transition!r}"
assert data == "some data here", f"Expected data 'some data here', got {data!r}"
print("  PASSED")


print("Test 35b: _normalize_response fixes bracket variants")
executor_n = DFAExecutor(make_graph())
bracket_response = """[state_machine_output]
<transition>next</transition>
<data>some data</data>
[/state_machine_output]"""
transition_n, data_n = executor_n._parse_response(bracket_response)
assert transition_n == "next", f"Expected 'next', got {transition_n!r}"
assert data_n == "some data", f"Expected 'some data', got {data_n!r}"
print("  PASSED")


print("Test 35c: _normalize_response fixes transition_to tag alias")
executor_n2 = DFAExecutor(make_graph())
alias_response = """<state_machine_output>
<transition_to>Experiment Runner</transition_to>
<data>test data</data>
</state_machine_output>"""
transition_n2, data_n2 = executor_n2._parse_response(alias_response)
assert transition_n2 == "Experiment Runner", f"Expected 'Experiment Runner', got {transition_n2!r}"
assert data_n2 == "test data", f"Expected 'test data', got {data_n2!r}"
print("  PASSED")


print("Test 35d: _normalize_response fixes summary tag alias")
executor_n3 = DFAExecutor(make_graph())
summary_response = """<state_machine_output>
<transition>next</transition>
<summary>summary content</summary>
</state_machine_output>"""
transition_n3, data_n3 = executor_n3._parse_response(summary_response)
assert transition_n3 == "next", f"Expected 'next', got {transition_n3!r}"
assert data_n3 == "summary content", f"Expected 'summary content', got {data_n3!r}"
print("  PASSED")


print("Test 35e: _normalize_response fixes combined bracket + alias (worst case)")
executor_n4 = DFAExecutor(make_graph())
worst_case = """[state_machine_output]
<transition_to>Experiment Runner</transition_to>
<summary>Designed test script</summary>
[/state_machine_output]"""
transition_n4, data_n4 = executor_n4._parse_response(worst_case)
assert transition_n4 == "Experiment Runner", f"Expected 'Experiment Runner', got {transition_n4!r}"
assert data_n4 == "Designed test script", f"Expected 'Designed test script', got {data_n4!r}"
print("  PASSED")


# --- HAIKU MODEL SUPPORT TESTS ---

print("Test 42: _resolve_model returns Haiku when node model is set to Haiku")
graph_42 = make_graph()
graph_42["nodes"]["node1"]["model"] = "claude-haiku-4-5-20251001"
executor_42 = DFAExecutor(graph_42)
resolved_42 = executor_42._resolve_model(graph_42["nodes"]["node1"])
assert resolved_42 == "claude-haiku-4-5-20251001", \
    f"Expected 'claude-haiku-4-5-20251001', got {resolved_42!r}"
print("  PASSED")


print("Test 43: _resolve_model returns Haiku graph-level override (overriding node-level Opus)")
graph_43 = make_graph()
graph_43["model_override"] = "claude-haiku-4-5-20251001"
graph_43["nodes"]["node1"]["model"] = "claude-opus-4-6"
executor_43 = DFAExecutor(graph_43)
resolved_43 = executor_43._resolve_model(graph_43["nodes"]["node1"])
assert resolved_43 == "claude-haiku-4-5-20251001", \
    f"Expected graph-level Haiku override to win, got {resolved_43!r}"
print("  PASSED")


print("Test 44: _resolve_model returns node-level Haiku when graph override is 'individual'")
graph_44 = make_graph()
graph_44["model_override"] = "individual"
graph_44["nodes"]["node1"]["model"] = "claude-haiku-4-5-20251001"
executor_44 = DFAExecutor(graph_44)
resolved_44 = executor_44._resolve_model(graph_44["nodes"]["node1"])
assert resolved_44 == "claude-haiku-4-5-20251001", \
    f"Expected node-level Haiku when override is 'individual', got {resolved_44!r}"
print("  PASSED")


print("Test 45: MODEL_HAIKU constant is defined in executor module")
assert hasattr(executor_module, 'MODEL_HAIKU'), \
    "executor module should export MODEL_HAIKU constant"
assert executor_module.MODEL_HAIKU == "claude-haiku-4-5-20251001", \
    f"MODEL_HAIKU should be 'claude-haiku-4-5-20251001', got {executor_module.MODEL_HAIKU!r}"
print("  PASSED")


print("Test 46: Graph-level model dropdown in index.html includes Haiku option")
html = read_file("templates/index.html")
haiku_option = re.search(r'<option\s+value="claude-haiku-4-5-20251001"[^>]*>.*?[Hh]aiku.*?</option>', html)
assert haiku_option is not None, \
    "index.html graph-level model dropdown should have a Haiku option with value 'claude-haiku-4-5-20251001'"
print("  PASSED")


print("Test 47: Per-node model dropdown in index.html includes Haiku option")
html = read_file("templates/index.html")
# There should be at least two Haiku options (graph-level and node-level dropdowns)
haiku_options = re.findall(r'<option\s+value="claude-haiku-4-5-20251001"', html)
assert len(haiku_options) >= 2, \
    f"Expected at least 2 Haiku option elements (graph + node dropdowns), got {len(haiku_options)}"
print("  PASSED")


print("Test 48: app.js renders Haiku badge for nodes with Haiku model")
js = read_file("static/app.js")
assert "haiku" in js.lower(), \
    "app.js should contain Haiku badge rendering logic"
haiku_badge = re.search(r'["\']haiku["\']', js, re.IGNORECASE)
assert haiku_badge is not None, \
    "app.js should reference 'haiku' for badge CSS class"
print("  PASSED")


print("Test 49: style.css has .model-badge.haiku styling")
css = read_file("static/style.css")
haiku_css = re.search(r'\.model-badge\.haiku\s*\{', css)
assert haiku_css is not None, \
    "style.css should have .model-badge.haiku rule for Haiku badge styling"
print("  PASSED")


print("Test 50: _resolve_model returns default (Opus) when node has no model and no graph override")
graph_50 = make_graph()
# node1 has no 'model' key, graph has no 'model_override'
executor_50 = DFAExecutor(graph_50)
resolved_50 = executor_50._resolve_model(graph_50["nodes"]["node1"])
assert resolved_50 == executor_module.DEFAULT_MODEL, \
    f"Expected DEFAULT_MODEL ({executor_module.DEFAULT_MODEL!r}), got {resolved_50!r}"
print("  PASSED")


# Step 3 backend regression tests
from app import app, socketio
import executor as exec_mod


print("Test 36: handle_user_message calls send_user_message and returns 'sent' on success (regression)")
mock_exc = MagicMock()
mock_exc.send_user_message.return_value = True
with patch.object(exec_mod, 'get_execution', return_value=mock_exc):
    client = socketio.test_client(app)
    received_before = client.get_received()
    client.emit('user_message', {'execution_id': 'exec-1', 'message': 'hello'})
    received = client.get_received()
    ack_events = [r for r in received if r['name'] == 'message_ack']
    assert len(ack_events) == 1, f"Expected 1 message_ack event, got {len(ack_events)}: {received}"
    ack_data = ack_events[0]['args'][0]
    assert ack_data['status'] == 'sent', \
        f"Expected ack status 'sent' on success, got {ack_data['status']!r}"
    client.disconnect()
print("  PASSED")


print("Test 37: handle_user_message returns 'failed' when send_user_message returns False (regression)")
mock_exc = MagicMock()
mock_exc.send_user_message.return_value = False
with patch.object(exec_mod, 'get_execution', return_value=mock_exc):
    client = socketio.test_client(app)
    received_before = client.get_received()
    client.emit('user_message', {'execution_id': 'exec-2', 'message': 'test msg'})
    received = client.get_received()
    ack_events = [r for r in received if r['name'] == 'message_ack']
    assert len(ack_events) == 1, f"Expected 1 message_ack event, got {len(ack_events)}: {received}"
    ack_data = ack_events[0]['args'][0]
    assert ack_data['status'] == 'failed', \
        f"Expected ack status 'failed' when send fails, got {ack_data['status']!r}"
    client.disconnect()
print("  PASSED")


print("Test 38: handle_user_message emits error when execution_id is missing (regression)")
client = socketio.test_client(app)
received_before = client.get_received()
client.emit('user_message', {'message': 'hello'})
received = client.get_received()
error_events = [r for r in received if r['name'] == 'error']
assert len(error_events) == 1, f"Expected 1 error event for missing execution_id, got {len(error_events)}: {received}"
client.disconnect()
print("  PASSED")


# --- LARGE SCALE TESTS ---

print("Test 39: All interactive bar CSS properties are consistent with feedback bar pattern")
css = read_file("static/style.css")
# Count the number of interactive-bar rules - should have at least 5 distinct selectors
interactive_selectors = re.findall(r'\.interactive-bar[^\{]*\{', css)
assert len(interactive_selectors) >= 5, \
    f"Expected at least 5 .interactive-bar CSS selectors (base, .active, span, input, button), got {len(interactive_selectors)}: {interactive_selectors}"
print("  PASSED")


print("Test 40: app.js socket variable is used in multiple contexts (emit/on)")
js = read_file("static/app.js")
socket_emits = re.findall(r'socket\.emit\s*\(', js)
socket_ons = re.findall(r'socket\.on\s*\(', js)
assert len(socket_emits) >= 3, \
    f"Expected at least 3 socket.emit calls (user_message, subscribe, unsubscribe), got {len(socket_emits)}"
assert len(socket_ons) >= 1, \
    f"Expected at least 1 socket.on call (message_ack), got {len(socket_ons)}"
print("  PASSED")


print("Test 41: All three frontend files are self-consistent (IDs match across HTML and JS)")
html = read_file("templates/index.html")
js = read_file("static/app.js")
# Verify that all interactive bar element IDs referenced in JS exist in HTML
js_ids = re.findall(r'\$\s*\(\s*["\'](\w*[Ii]nteractive\w*)["\']', js)
for js_id in js_ids:
    assert f'id="{js_id}"' in html, \
        f"JS references element ID '{js_id}' but it's not found in index.html"
# Also check direct getElementById calls
js_ids2 = re.findall(r'getElementById\s*\(\s*["\'](\w*[Ii]nteractive\w*)["\']', js)
for js_id in js_ids2:
    assert f'id="{js_id}"' in html, \
        f"JS references element ID '{js_id}' via getElementById but it's not found in index.html"
# Verify CSS classes match
assert "interactive-bar" in css, "CSS should have interactive-bar class"
assert 'class="interactive-bar"' in html or "class='interactive-bar'" in html, \
    "HTML should use the interactive-bar class that CSS defines"
print("  PASSED")


print("\nALL TESTS PASSED")
