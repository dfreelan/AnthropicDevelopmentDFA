# Claude State Machine Builder - Implementation Plan

## Context
Build a Flask web app that lets users visually construct a DFA (state machine) where each state executes a `claude -p "prompt"` CLI call. The model decides which transition to take and passes XML data to the next state. This is the foundation for a hierarchical DFA system for orchestrating multi-step Claude workflows.

## Project Structure
```
PLACEHOLDER
├── app.py                # Flask app, all routes
├── executor.py           # DFA execution engine (subprocess + threading)
├── storage.py            # JSON file persistence
├── templates/
│   └── index.html        # Single-page app (all UI)
├── static/
│   ├── style.css         # All styles
│   └── app.js            # All frontend logic
└── data/
    └── graphs/           # Saved graph JSON files (auto-created)
```

## Data Model

### Graph (saved as `data/graphs/<id>.json`)
```json
{
  "id": "uuid",
  "name": "My Workflow",
  "nodes": {
    "node-uuid-1": {
      "id": "node-uuid-1",
      "name": "extract_info",
      "prompt": "Extract key entities from the following text...",
      "position": { "x": 200, "y": 150 },
      "transitions": [
        {
          "id": "tr-uuid",
          "target_state": "summarize",
          "label": "success",
          "data_template": "<entities>\n  <!-- Claude fills this -->\n</entities>"
        },
        {
          "id": "tr-uuid-2",
          "target_state": "handle_error",
          "label": "error",
          "data_template": "<error>\n  <!-- error info -->\n</error>"
        }
      ]
    }
  },
  "start_node": "node-uuid-1"
}
```

### Execution State (in-memory, not persisted)
```json
{
  "execution_id": "uuid",
  "graph_id": "uuid",
  "status": "running|paused_for_feedback|completed|error",
  "current_node": "node-uuid",
  "history": [
    {
      "node_id": "...",
      "node_name": "...",
      "prompt_sent": "...",
      "raw_response": "...",
      "transition_taken": "success",
      "data_passed": "<entities>...</entities>",
      "timestamp": "..."
    }
  ],
  "pending_question": "What format should the output be in?",
  "incoming_data": "<previous state's XML>"
}
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Serve index.html |
| `GET` | `/api/graphs` | List all saved graphs |
| `POST` | `/api/graphs` | Create new graph |
| `GET` | `/api/graphs/<id>` | Load a graph |
| `PUT` | `/api/graphs/<id>` | Save full graph state (auto-save target) |
| `DELETE` | `/api/graphs/<id>` | Delete a graph |
| `POST` | `/api/execute/<graph_id>` | Start DFA execution from start_node (optional: `{"input_data": "<xml>..."}`) |
| `GET` | `/api/execution/<exec_id>` | Poll execution status + history |
| `POST` | `/api/execution/<exec_id>/feedback` | Submit human feedback response |
| `POST` | `/api/execution/<exec_id>/stop` | Stop a running execution |

## Frontend Architecture

### Layout
```
+----------------------------------------------------------+
| toolbar: [graph name] [New Node] [Run] [Save] [Load]     |
+------------+---------------------------------------------+
|            |                                             |
| Side Panel |          Canvas (SVG + divs)                |
| (edit form)|                                             |
|            |                                             |
|            |    [node1] ----> [node2]                    |
|            |       \                                     |
|            |        +--> [node3]                         |
|            |                                             |
+------------+---------------------------------------------+
| Execution log / human feedback prompt (collapsible)      |
+----------------------------------------------------------+
```

### Node Rendering
- Each node is an absolutely-positioned `<div>` inside a container div (the canvas)
- Nodes are draggable via mousedown/mousemove/mouseup on a drag handle
- Node shows: name (centered text), small colored dot if it's the start node
- Selected node gets a highlight border
- Canvas is pannable (translate the container) and optionally zoomable

### Connection Rendering
- An `<svg>` element overlays the entire canvas at the same size
- Each transition draws an SVG `<path>` (cubic bezier) from source node to target node
- Arrows via SVG `<marker>` for arrowheads
- Paths update whenever a node is dragged
- Transition label shown at midpoint of the path

### Side Panel Behavior
- **No node selected**: Shows "New Node" button, graph settings (name, start node dropdown)
- **Node selected**: Shows edit form:
  - **Name**: text input
  - **Prompt**: textarea (monospace, resizable)
  - _(Future: "Enhance Prompt" button - placeholder, disabled for now)_
  - **Transitions section**:
    - List of current transitions, each showing:
      - Label (text input, e.g. "success", "error")
      - Target state (text input with autocomplete from existing node names)
      - Data template (textarea, XML)
      - Delete button (x)
    - "+ Add Transition" button at bottom
  - **Delete Node** button (with confirmation)
  - **Set as Start Node** button

### Auto-Create Nodes
- When a transition's target_state is typed and the user tabs/blurs out, if no node with that name exists, auto-create one at a position offset from the current node
- The new node appears on canvas immediately with the typed name
- A connection line is drawn

### Auto-Save
- On every meaningful change (node edit, drag, transition add/remove), debounce 500ms then `PUT /api/graphs/<id>`
- The full graph JSON is sent each time (simple, no partial updates)
- Visual indicator: small "Saved" / "Saving..." text in toolbar

## Execution Engine (`executor.py`)

### Prompt Construction
For each state, the full prompt sent to Claude is:

```
{user's state prompt}

---
You are a node in a state machine. After completing the task above, you MUST end your response with a structured output block in exactly this format:

<state_machine_output>
  <transition>TRANSITION_LABEL</transition>
  <data>
    YOUR XML DATA HERE
  </data>
</state_machine_output>

Available transitions (choose exactly one):
- success: proceeds to "summarize" node
- error: proceeds to "handle_error" node
- __human_feedback__: use ONLY if the task is genuinely ambiguous and you need clarification from the human operator. Put your question in the <data> tag.

Data from the previous state:
<previous_state_data>
{xml data from previous transition, or "None - this is the start state"}
</previous_state_data>
```

### Execution Flow
1. `POST /api/execute/<graph_id>` starts a background thread
2. Thread loops:
   a. Build full prompt for current state
   b. Run `subprocess.run(['claude', '-p', full_prompt], capture_output=True, text=True, timeout=300)`
   c. Parse response: extract `<state_machine_output>` block via regex
   d. If transition is `__human_feedback__`: set status to `paused_for_feedback`, store question, wait on a `threading.Event`
   e. Otherwise: look up target node from transition label, store history entry, move to next node
   f. If target node doesn't exist or transition label is invalid: set status to `error`
   g. If no more transitions (terminal node with no transitions): set status to `completed`
3. Frontend polls `GET /api/execution/<exec_id>` every 2 seconds
4. When `paused_for_feedback`: frontend shows the question + text input, user submits via `POST /api/execution/<exec_id>/feedback`, which appends the answer to the data and resumes

### Human Feedback
- The `__human_feedback__` transition is NOT shown in the node editor - it's always implicitly available
- When Claude uses it, the `<data>` contains the question
- The frontend shows a notification bar at the bottom with the question and a text input
- User's answer gets wrapped as `<human_feedback_response>answer</human_feedback_response>` and appended to the incoming data for the SAME state (re-run the same node with the feedback)

## Implementation Steps

### Step 1: Project scaffolding
- Create directory structure
- Create `app.py` with Flask app, CORS, static/template config
- Create `storage.py` with JSON file read/write helpers
- Create empty `executor.py`
- Create `templates/index.html` with basic layout skeleton
- Create `static/style.css` with layout styles
- Create `static/app.js` with module structure

### Step 2: Backend API (storage + CRUD)
- Implement `storage.py`: `save_graph()`, `load_graph()`, `list_graphs()`, `delete_graph()`
- Implement all `/api/graphs/...` routes in `app.py`
- Auto-create `data/graphs/` directory on startup

### Step 3: Frontend - Canvas + Node rendering
- Implement node creation (New Node button -> API call -> render div)
- Implement node dragging (mousedown/mousemove/mouseup)
- Implement node selection (click -> highlight + show side panel)
- Implement canvas pan (mousedown on empty space + drag)
- Render nodes from loaded graph state

### Step 4: Frontend - Side panel + editing
- Build the edit form (name, prompt, transitions list)
- Implement transition add/remove
- Implement auto-create node when typing a new target state name
- Implement auto-save with debounce
- Implement delete node

### Step 5: Frontend - SVG connections
- Draw bezier curves between connected nodes
- Arrowheads via SVG markers
- Update paths on node drag
- Transition labels at path midpoints

### Step 6: Execution engine
- Implement `executor.py` with `DFAExecutor` class
- Background thread execution
- Prompt construction with template
- Response parsing (regex for `<state_machine_output>`)
- Execution state tracking
- Human feedback pause/resume mechanism

### Step 7: Execution UI
- Run button starts execution
- Bottom panel shows execution log (which node, what happened)
- Human feedback notification + input
- Stop button

### Step 8: Polish
- Start node indicator (visual marker)
- Graph name editing
- Multiple graphs (load/switch)
- Error handling and edge cases
- Keyboard shortcuts (Delete for selected node, Escape to deselect)

## Verification
1. Start the app: `cd PLACEHOLDER && python3 app.py`
2. Open browser to `http://localhost:5050`
3. Create a new node, name it, add a prompt
4. Add transitions, verify auto-created target nodes appear
5. Drag nodes around, verify connections follow
6. Refresh the page - verify everything persists
7. Create a simple 2-node graph and run it - verify Claude is called and transitions work
8. Test human feedback flow - verify pause/resume works
