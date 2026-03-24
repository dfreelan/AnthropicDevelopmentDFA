"""Flask web app for the Claude State Machine Builder."""

import os
import subprocess
import sys
import tempfile
import uuid
from flask import Flask, jsonify, request, render_template
from flask_socketio import SocketIO, emit, join_room, leave_room

import storage
import executor
import git_ops
import solution

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

SERVER_ID = str(uuid.uuid4())
SERVER_PORT = 5050

# Snapshot file mtimes at startup for change detection
import glob as _glob
_WATCH_PATTERNS = ['*.py', 'static/*.js', 'static/*.css', 'templates/*.html']
_STARTUP_MTIMES = {}
for _pat in _WATCH_PATTERNS:
    for _f in _glob.glob(_pat):
        _STARTUP_MTIMES[_f] = os.path.getmtime(_f)


@app.route("/api/server-id")
def server_id():
    return jsonify({"server_id": SERVER_ID})


@app.route("/api/code-changed")
def code_changed():
    for pat in _WATCH_PATTERNS:
        for f in _glob.glob(pat):
            current_mtime = os.path.getmtime(f)
            if f not in _STARTUP_MTIMES or current_mtime != _STARTUP_MTIMES[f]:
                return jsonify({"changed": True})
    return jsonify({"changed": False})


# --- Page ---
@app.route("/")
def index():
    return render_template("index.html", server_port=SERVER_PORT)


# --- Graph CRUD ---
@app.route("/api/graphs", methods=["GET"])
def list_graphs():
    return jsonify(storage.list_graphs())


@app.route("/api/graphs", methods=["POST"])
def create_graph():
    data = request.get_json() or {}
    graph = {
        "id": str(uuid.uuid4()),
        "name": data.get("name", "Untitled Workflow"),
        "nodes": {},
        "start_node": None,
        "working_directory": data.get("working_directory") or os.getcwd(),
    }
    storage.save_graph(graph)
    return jsonify(graph), 201


@app.route("/api/graphs/<graph_id>", methods=["GET"])
def get_graph(graph_id):
    graph = storage.load_graph(graph_id)
    if not graph:
        return jsonify({"error": "Graph not found"}), 404
    return jsonify(graph)


@app.route("/api/graphs/<graph_id>", methods=["PUT"])
def update_graph(graph_id):
    data = request.get_json()
    if not data or data.get("id") != graph_id:
        return jsonify({"error": "Invalid graph data"}), 400
    storage.save_graph(data)
    return jsonify(data)


@app.route("/api/graphs/<graph_id>", methods=["DELETE"])
def delete_graph(graph_id):
    if storage.delete_graph(graph_id):
        return jsonify({"ok": True})
    return jsonify({"error": "Graph not found"}), 404


# --- Node Library ---
@app.route("/api/nodes/library", methods=["GET"])
def node_library():
    exclude = request.args.get("exclude")
    return jsonify(storage.get_all_nodes_except(exclude))


@app.route("/api/check-directory")
def check_directory():
    path = request.args.get("path", "")
    if not path:
        return jsonify({"exists": False})
    return jsonify({"exists": os.path.isdir(path)})


# --- Working directory history ---
@app.route("/api/working-dirs", methods=["GET"])
def get_working_dirs():
    return jsonify(storage.load_working_dirs())


@app.route("/api/working-dirs", methods=["POST"])
def add_working_dir():
    data = request.get_json() or {}
    dir_path = (data.get("dir") or "").strip()
    if not dir_path:
        return jsonify({"error": "No dir provided"}), 400
    dirs = storage.load_working_dirs()
    dirs = [d for d in dirs if d != dir_path]
    dirs.insert(0, dir_path)
    dirs = dirs[:20]
    storage.save_working_dirs(dirs)
    return jsonify(dirs)


@app.route("/api/working-dirs", methods=["DELETE"])
def remove_working_dir():
    data = request.get_json() or {}
    dir_path = (data.get("dir") or "").strip()
    if not dir_path:
        return jsonify({"error": "No dir provided"}), 400
    dirs = storage.load_working_dirs()
    dirs = [d for d in dirs if d != dir_path]
    storage.save_working_dirs(dirs)
    return jsonify(dirs)


ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


# --- Execution ---
@app.route("/api/execute/<graph_id>", methods=["POST"])
def execute_graph(graph_id):
    graph = storage.load_graph(graph_id)
    if not graph:
        return jsonify({"error": "Graph not found"}), 404
    if not graph.get("start_node"):
        return jsonify({"error": "No start node set"}), 400

    image_paths = []
    temp_dir = None

    if request.content_type and request.content_type.startswith("multipart/form-data"):
        input_data = request.form.get("input_data", "") or None
        include_original_query = request.form.get("include_original_query") == "true"
        files = request.files.getlist("images")
        if files:
            temp_dir = tempfile.mkdtemp(prefix="dfa_images_")
            for f in files:
                ext = os.path.splitext(f.filename)[1].lower()
                if ext not in ALLOWED_IMAGE_EXTENSIONS:
                    continue
                safe_name = f"{uuid.uuid4()}_{f.filename}"
                path = os.path.join(temp_dir, safe_name)
                f.save(path)
                image_paths.append(path)
    else:
        data = request.get_json() or {}
        input_data = data.get("input_data")
        include_original_query = bool(data.get("include_original_query"))

    exc = executor.start_execution(graph, input_data, image_paths=image_paths, temp_dir=temp_dir, include_original_query=include_original_query)
    return jsonify({"execution_id": exc.execution_id}), 201


@app.route("/api/execution/<exec_id>", methods=["GET"])
def get_execution(exec_id):
    exc = executor.get_execution(exec_id)
    if not exc:
        return jsonify({"error": "Execution not found"}), 404
    return jsonify(exc.get_state())


@app.route("/api/execution/<exec_id>/feedback", methods=["POST"])
def submit_feedback(exec_id):
    exc = executor.get_execution(exec_id)
    if not exc:
        return jsonify({"error": "Execution not found"}), 404
    data = request.get_json() or {}
    response = data.get("response", "")
    exc.submit_feedback(response)
    return jsonify({"ok": True})


@app.route("/api/execution/<exec_id>/label", methods=["GET"])
def get_execution_label(exec_id):
    exc = executor.get_execution(exec_id)
    if not exc:
        return jsonify({"error": "Execution not found"}), 404
    input_text = exc.incoming_data or ""
    if not input_text or input_text == "None - this is the start state":
        return jsonify({"label": "New Run"})
    try:
        _env = os.environ.copy()
        _env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "128000"
        result = subprocess.run(
            [executor.CLAUDE_BIN, "-p", "--max-tokens", "10",
             "Summarize this task in exactly 2 words. Reply with ONLY those 2 words, nothing else:\n" + input_text[:500]],
            capture_output=True, text=True, timeout=15, env=_env
        )
        label = result.stdout.strip().split('\n')[0][:30]
        if not label:
            label = "New Run"
    except Exception:
        words = [w for w in input_text.split() if len(w) > 2][:2]
        label = ' '.join(words) if words else "New Run"
    return jsonify({"label": label})


@app.route("/api/execution/<exec_id>/end-interactive", methods=["POST"])
def end_interactive(exec_id):
    exc = executor.get_execution(exec_id)
    if not exc:
        return jsonify({"error": "not found"}), 404
    exc.end_interactive()
    return jsonify({"status": "completed"})


@app.route("/api/execution/<exec_id>/stop", methods=["POST"])
def stop_execution(exec_id):
    if executor.stop_execution(exec_id):
        return jsonify({"ok": True})
    return jsonify({"error": "Execution not found"}), 404


@app.route("/api/graph/<graph_id>/active-execution", methods=["GET"])
def get_active_execution(graph_id):
    exc = executor.get_active_execution_for_graph(graph_id)
    if not exc:
        return jsonify({"active": False}), 200
    return jsonify({"active": True, "execution_id": exc.execution_id, "status": exc.status})


# --- Git Timeline / Undo / Redo ---
@app.route("/api/graphs/<graph_id>/timeline", methods=["GET"])
def get_timeline(graph_id):
    """Get git timeline state for a graph."""
    return jsonify(git_ops.get_timeline_state(graph_id))


@app.route("/api/graphs/<graph_id>/undo", methods=["POST"])
def undo(graph_id):
    """Undo to previous git snapshot."""
    graph = storage.load_graph(graph_id)
    if not graph:
        return jsonify({"error": "Graph not found"}), 404
    cwd = graph.get("working_directory", "")
    if not cwd or not git_ops.is_git_repo(cwd):
        return jsonify({"error": "No git repo at working directory"}), 400
    result = git_ops.undo(graph_id, cwd)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/graphs/<graph_id>/redo", methods=["POST"])
def redo(graph_id):
    """Redo to next git snapshot."""
    graph = storage.load_graph(graph_id)
    if not graph:
        return jsonify({"error": "Graph not found"}), 404
    cwd = graph.get("working_directory", "")
    if not cwd or not git_ops.is_git_repo(cwd):
        return jsonify({"error": "No git repo at working directory"}), 400
    result = git_ops.redo(graph_id, cwd)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/reboot", methods=["POST"])
def reboot_server():
    """Restart the server process via subprocess, then exit."""
    import subprocess
    app_path = os.path.abspath(sys.argv[0])
    app_dir = os.path.dirname(app_path)
    subprocess.Popen([sys.executable, app_path, "--port", str(SERVER_PORT)],
                     cwd=app_dir,
                     start_new_session=True,
                     stdin=subprocess.DEVNULL,
                     stdout=open(os.path.join(app_dir, "server.log"), "a"),
                     stderr=subprocess.STDOUT)
    # Give the response time to send, then exit so port is freed
    import threading
    def do_exit():
        import time
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=do_exit, daemon=True).start()
    return jsonify({"status": "rebooting"})


# --- WebSocket Events ---
@socketio.on('connect')
def handle_connect():
    print(f"[SocketIO] Client connected")


@socketio.on('disconnect')
def handle_disconnect():
    print(f"[SocketIO] Client disconnected")


@socketio.on('subscribe_execution')
def handle_subscribe(data):
    execution_id = data.get('execution_id') if data else None
    if execution_id:
        join_room(execution_id)
        print(f"[SocketIO] Client subscribed to execution {execution_id}")


@socketio.on('unsubscribe_execution')
def handle_unsubscribe(data):
    execution_id = data.get('execution_id') if data else None
    if execution_id:
        leave_room(execution_id)
        print(f"[SocketIO] Client unsubscribed from execution {execution_id}")


@socketio.on('user_message')
def handle_user_message(data):
    execution_id = data.get('execution_id') if data else None
    message = data.get('message') if data else None
    if not execution_id or not message:
        emit('error', {'error': 'Missing execution_id or message'})
        return
    exc = executor.get_execution(execution_id)
    if not exc:
        emit('error', {'error': f'Execution {execution_id} not found'})
        return
    success = exc.send_user_message(message)
    emit('message_ack', {'execution_id': execution_id, 'status': 'sent' if success else 'failed'})


if __name__ == "__main__":
    import argparse
    import time as _time, socket as _socket
    _parser = argparse.ArgumentParser()
    _parser.add_argument("--port", type=int, default=5050)
    _args = _parser.parse_args()
    SERVER_PORT = _args.port
    storage.ensure_data_dir()
    if not any(g["name"] == "Plan and Iterate HFA" for g in storage.list_graphs()):
        _tpl = solution.generate_graph()
        _tpl["working_directory"] = os.getcwd()
        storage.save_graph(_tpl)
    if not any(g["name"] == "DFA Builder" for g in storage.list_graphs()):
        _tpl2 = solution.generate_dfa_builder_graph()
        _tpl2["working_directory"] = os.getcwd()
        storage.save_graph(_tpl2)
    # Wait for port to be free (handles reboots)
    for _attempt in range(15):
        _s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        try:
            _s.bind(("127.0.0.1", SERVER_PORT))
            _s.close()
            break
        except OSError:
            _s.close()
            _time.sleep(1)
    socketio.run(app, debug=True, use_reloader=False, port=SERVER_PORT, allow_unsafe_werkzeug=True)
