import uuid


def generate_graph():
    """Generate a Plan-and-Iterate HFA graph.

    Top-level workflow:
      Planner -> Step Iterator -> AgentCoder (HFA) -> Check Progress -> Final Output
                       ^                                    |
                       |__________ (more steps) ___________|

    The AgentCoder HFA node contains a child_graph with:
      Test Designer -> Programmer -> Test Executor -> (retry loop) -> Output Result
    """

    def uid():
        return str(uuid.uuid4())

    # --- Top-level node IDs ---
    planner_id = uid()
    iterator_id = uid()
    agent_coder_id = uid()
    check_progress_id = uid()
    output_id = uid()

    # --- AgentCoder child_graph node IDs ---
    td_id = uid()
    prog_id = uid()
    te_id = uid()
    child_output_id = uid()

    # --- Build AgentCoder child_graph ---
    child_graph = {
        "start_node": td_id,
        "nodes": {
            td_id: {
                "id": td_id,
                "name": "Test Designer Agent",
                "prompt": (
                    "You are the Test Designer Agent in the AgentCoder multi-agent framework. "
                    "Your job is to generate comprehensive test cases for a coding task without "
                    "seeing any solution code. Write tests to tests.py using assert statements. "
                    "Include basic tests, edge case tests, and large scale tests. "
                    "End with print('ALL TESTS PASSED')."
                ),
                "position": {"x": 100, "y": 150},
                "transitions": [
                    {
                        "id": uid(),
                        "label": "Generate code",
                        "target_state": "Programmer Agent",
                        "data_template": "",
                    }
                ],
                "persistent_context": False,
                "is_hfa": False,
            },
            prog_id: {
                "id": prog_id,
                "name": "Programmer Agent",
                "prompt": (
                    "You are the Programmer Agent. Your job is to write or refine a "
                    "solution for the given task. Use Chain-of-Thought reasoning to understand "
                    "the problem, select an approach, write pseudocode, then implement. "
                    "Write your solution to appropriately named files in the working directory."
                ),
                "position": {"x": 350, "y": 150},
                "transitions": [
                    {
                        "id": uid(),
                        "label": "Run tests",
                        "target_state": "Test Executor Agent",
                        "data_template": "",
                    }
                ],
                "persistent_context": True,
                "is_hfa": False,
            },
            te_id: {
                "id": te_id,
                "name": "Test Executor Agent",
                "prompt": (
                    "You are the Test Executor Agent. Run the tests with: "
                    "python3 tests.py 2>&1. If ALL TESTS PASSED, transition to Done. "
                    "If tests fail and iteration budget remains, transition to Retry. "
                    "If budget exhausted, transition to Done."
                ),
                "position": {"x": 600, "y": 200},
                "transitions": [
                    {
                        "id": uid(),
                        "label": "Retry",
                        "target_state": "Programmer Agent",
                        "data_template": "",
                    },
                    {
                        "id": uid(),
                        "label": "Done",
                        "target_state": "Output Result",
                        "data_template": "",
                    },
                ],
                "persistent_context": True,
                "is_hfa": False,
            },
            child_output_id: {
                "id": child_output_id,
                "name": "Output Result",
                "prompt": (
                    "You are the final output node of the AgentCoder pipeline. "
                    "Present the results: status, iterations used, and final solution code."
                ),
                "position": {"x": 900, "y": 150},
                "transitions": [],
                "persistent_context": False,
                "is_hfa": False,
            },
        },
    }

    # --- Build top-level graph ---
    graph = {
        "id": uid(),
        "name": "Plan and Iterate HFA",
        "start_node": planner_id,
        "nodes": {
            planner_id: {
                "id": planner_id,
                "name": "Planner",
                "prompt": (
                    "You are the Planner node. You receive an idea or task description as input. "
                    "Your job is to break it down into a step-by-step plan of incremental coding "
                    "steps. Each step should be small enough that only a few tests are needed to "
                    "validate it. Output a numbered list of steps. The first step should be the "
                    "simplest foundation, and each subsequent step builds on the previous ones."
                ),
                "position": {"x": 100, "y": 300},
                "transitions": [
                    {
                        "id": uid(),
                        "label": "Plan ready",
                        "target_state": "Step Iterator",
                        "data_template": "",
                    }
                ],
                "persistent_context": False,
                "is_hfa": False,
            },
            iterator_id: {
                "id": iterator_id,
                "name": "Step Iterator",
                "prompt": (
                    "You are the Step Iterator. You receive a plan (numbered list of steps) and "
                    "progress so far. Extract the next incomplete step from the plan and dispatch "
                    "it to the AgentCoder HFA for implementation. Pass the step description, any "
                    "context from previous steps, and the overall plan as data."
                ),
                "position": {"x": 350, "y": 300},
                "transitions": [
                    {
                        "id": uid(),
                        "label": "Execute step",
                        "target_state": "AgentCoder",
                        "data_template": "",
                    }
                ],
                "persistent_context": True,
                "is_hfa": False,
            },
            agent_coder_id: {
                "id": agent_coder_id,
                "name": "AgentCoder",
                "prompt": (
                    "AgentCoder HFA sub-graph: implements a single coding step using the "
                    "Test Designer -> Programmer -> Test Executor pipeline with retry loop."
                ),
                "position": {"x": 600, "y": 300},
                "transitions": [
                    {
                        "id": uid(),
                        "label": "Step complete",
                        "target_state": "Check Progress",
                        "data_template": "",
                    }
                ],
                "persistent_context": False,
                "is_hfa": True,
                "child_graph": child_graph,
            },
            check_progress_id: {
                "id": check_progress_id,
                "name": "Check Progress",
                "prompt": (
                    "You are the Check Progress node. Review the plan and determine if all steps "
                    "have been completed. If there are more steps remaining, transition back to "
                    "the Step Iterator with 'More steps'. If all steps are done, transition to "
                    "'All done' to produce the final output."
                ),
                "position": {"x": 850, "y": 300},
                "transitions": [
                    {
                        "id": uid(),
                        "label": "More steps",
                        "target_state": "Step Iterator",
                        "data_template": "",
                    },
                    {
                        "id": uid(),
                        "label": "All done",
                        "target_state": "Final Output",
                        "data_template": "",
                    },
                ],
                "persistent_context": True,
                "is_hfa": False,
            },
            output_id: {
                "id": output_id,
                "name": "Final Output",
                "prompt": (
                    "You are the Final Output node. Present a summary of all completed steps, "
                    "the final integrated solution, and any notes about the implementation."
                ),
                "position": {"x": 1100, "y": 300},
                "transitions": [],
                "persistent_context": False,
                "is_hfa": False,
            },
        },
    }

    return graph


def generate_dfa_builder_graph():
    """Generate a DFA Builder meta-DFA graph.

    Workflow:
      Plan DFA -> Generate DFA JSON -> Validate DFA -> Report Result (terminal)
                        ^                    |
                        |___ (Needs fixes) __|
    """

    def uid():
        return str(uuid.uuid4())

    # --- Node IDs ---
    plan_id = uid()
    generate_id = uid()
    validate_id = uid()
    report_id = uid()

    graph = {
        "id": uid(),
        "name": "DFA Builder",
        "start_node": plan_id,
        "nodes": {
            plan_id: {
                "id": plan_id,
                "name": "Plan DFA",
                "prompt": (
                    "You are the DFA Architecture Planner. Your job is to design a complete "
                    "DFA (Deterministic Finite Automaton) workflow graph based on the user's "
                    "request.\n\n"
                    "IMPORTANT BACKGROUND - How DFAs work in this project:\n"
                    "This project is a Flask web app that lets users build state machine "
                    "workflows where each node is a separate Claude CLI invocation. Key "
                    "mechanics:\n\n"
                    "1. NODES: Each node has a \"prompt\" field that tells Claude what to do. "
                    "Each node runs as a completely independent Claude session with ZERO "
                    "memory of other nodes. The ONLY way data passes between nodes is via "
                    "XML data blocks.\n\n"
                    "2. TRANSITIONS: Each node has a list of transitions with labels. After "
                    "completing its work, Claude must output a [state_machine_output] XML "
                    "block choosing one transition and passing data to the next node. A "
                    "node with no transitions is a terminal node (ends the DFA).\n\n"
                    "3. DATA PASSING: The next node receives the previous node's full "
                    "response AND the explicit <data> content. Each node prompt must be "
                    "completely self-contained — it cannot assume the reader knows anything "
                    "about prior nodes.\n\n"
                    "4. PERSISTENT CONTEXT: If a node has persistent_context=true, it will "
                    "see all its own prior outputs in a <persistent_node_context> block. "
                    "This is essential for retry/iteration loops so the node can see what "
                    "it tried before and what went wrong.\n\n"
                    "5. ORIGINAL QUERY: The user can check \"Include original query\" when "
                    "running the DFA, which injects the user's initial input into every "
                    "node via an <original_user_query> block.\n\n"
                    "6. WORKING DIRECTORY: The DFA has a working_directory where all Claude "
                    "CLI calls execute. All file operations happen relative to this "
                    "directory.\n\n"
                    "YOUR TASK:\n"
                    "Based on the user's request (in <original_user_query> or "
                    "<previous_state_data>), design a complete DFA architecture. For each "
                    "node specify:\n"
                    "- Node name (short, descriptive)\n"
                    "- Purpose and responsibilities (detailed)\n"
                    "- Whether it needs persistent_context (true only for nodes in retry "
                    "loops)\n"
                    "- Complete prompt text (must be self-contained, explain the node's "
                    "role, what input it receives, what output it should produce, and what "
                    "transitions are available)\n"
                    "- Transitions (label and target node name)\n"
                    "- Data flow: what data should be passed between nodes\n\n"
                    "CRITICAL RULES FOR PROMPT DESIGN:\n"
                    "- Every prompt must be COMPLETELY self-contained. The Claude instance "
                    "reading it knows NOTHING about the DFA system, other nodes, or prior "
                    "context beyond what's in the prompt text and the data blocks.\n"
                    "- Prompts should explain the node's role, expected input format, "
                    "expected output, and available transitions.\n"
                    "- Never use literal [state_machine_output] tags inside prompt text "
                    "(the system adds those instructions automatically).\n"
                    "- Include enough detail that a human reading just the prompt would "
                    "know exactly what to do.\n\n"
                    "Do NOT write any code or read any files. Focus purely on architectural "
                    "design.\n"
                    "Output a complete, detailed DFA design document that the next node "
                    "can use to generate the JSON."
                ),
                "position": {"x": 100, "y": 200},
                "transitions": [
                    {
                        "id": uid(),
                        "label": "Plan ready",
                        "target_state": "Generate DFA JSON",
                        "data_template": "",
                    }
                ],
                "persistent_context": False,
                "is_hfa": False,
            },
            generate_id: {
                "id": generate_id,
                "name": "Generate DFA JSON",
                "prompt": (
                    "You are the DFA JSON Generator. Your job is to take a DFA "
                    "architecture plan and produce a valid graph JSON file that this "
                    "project can load.\n\n"
                    "EXACT JSON SCHEMA for graph files:\n"
                    "{\n"
                    "  \"id\": \"<uuid4 string>\",\n"
                    "  \"name\": \"<graph display name>\",\n"
                    "  \"start_node\": \"<uuid4 of the start node>\",\n"
                    "  \"working_directory\": \"\",\n"
                    "  \"nodes\": {\n"
                    "    \"<node_uuid>\": {\n"
                    "      \"id\": \"<same node_uuid>\",\n"
                    "      \"name\": \"<display name>\",\n"
                    "      \"prompt\": \"<the full prompt text for this node>\",\n"
                    "      \"position\": {\"x\": <number>, \"y\": <number>},\n"
                    "      \"transitions\": [\n"
                    "        {\n"
                    "          \"id\": \"<transition_uuid>\",\n"
                    "          \"label\": \"<transition label>\",\n"
                    "          \"target_state\": \"<TARGET NODE NAME (not ID)>\",\n"
                    "          \"data_template\": \"\"\n"
                    "        }\n"
                    "      ],\n"
                    "      \"persistent_context\": <true or false>,\n"
                    "      \"is_hfa\": false\n"
                    "    }\n"
                    "  }\n"
                    "}\n\n"
                    "FIELD RULES:\n"
                    "- All IDs (graph id, node ids, transition ids) must be valid UUID4 "
                    "strings. Generate them with uuid.uuid4() or equivalent.\n"
                    "- \"start_node\" must be the UUID of one of the nodes in the nodes "
                    "dict.\n"
                    "- Node dict keys must equal the node's \"id\" field.\n"
                    "- \"target_state\" in transitions uses the TARGET NODE'S NAME "
                    "(string), NOT its UUID.\n"
                    "- \"data_template\" should be an empty string \"\".\n"
                    "- \"is_hfa\" should be false for all nodes (unless the plan "
                    "specifically calls for hierarchical sub-graphs).\n"
                    "- \"working_directory\" should be an empty string (set at runtime).\n"
                    "- Space nodes out horizontally: x positions at 100, 400, 700, 1000, "
                    "etc. y positions around 200.\n\n"
                    "PROMPT WRITING RULES:\n"
                    "- Each node's prompt must be fully self-contained and explain the "
                    "node's role completely.\n"
                    "- NEVER include literal [state_machine_output] XML tags inside any "
                    "prompt. The DFA engine appends transition instructions "
                    "automatically.\n"
                    "- Prompts should be substantial (multiple paragraphs) — not "
                    "one-liners.\n"
                    "- Include what input the node expects, what it should do, and what "
                    "its output should be.\n\n"
                    "STEPS:\n"
                    "1. Read the DFA plan from the incoming data.\n"
                    "2. Generate valid UUIDs for the graph and each node.\n"
                    "3. Write the complete JSON to a file: data/graphs/<graph_id>.json\n"
                    "4. If you see a <persistent_node_context> block, that means "
                    "validation failed previously. Read the feedback carefully and fix "
                    "the SPECIFIC issues identified rather than regenerating from "
                    "scratch.\n\n"
                    "Use the Write tool or python to create the JSON file. Make sure the "
                    "JSON is valid and parseable."
                ),
                "position": {"x": 400, "y": 200},
                "transitions": [
                    {
                        "id": uid(),
                        "label": "DFA generated",
                        "target_state": "Validate DFA",
                        "data_template": "",
                    }
                ],
                "persistent_context": True,
                "is_hfa": False,
            },
            validate_id: {
                "id": validate_id,
                "name": "Validate DFA",
                "prompt": (
                    "You are the DFA Validator. Your job is to read a generated DFA "
                    "graph JSON file and validate it thoroughly.\n\n"
                    "Find the most recently created JSON file in data/graphs/ (the one "
                    "mentioned in the incoming data), read it, and check ALL of the "
                    "following:\n\n"
                    "STRUCTURAL CHECKS:\n"
                    "- Valid JSON (parseable)\n"
                    "- Has required top-level fields: id, name, start_node, nodes\n"
                    "- \"id\" is a valid UUID string\n"
                    "- \"start_node\" is a UUID that exists as a key in the \"nodes\" "
                    "dict\n"
                    "- \"nodes\" is a non-empty dict\n\n"
                    "PER-NODE CHECKS:\n"
                    "- Each node has: id, name, prompt, position, transitions, "
                    "persistent_context, is_hfa\n"
                    "- Node dict key matches the node's \"id\" field\n"
                    "- position has \"x\" and \"y\" numeric fields\n"
                    "- transitions is an array (can be empty for terminal nodes)\n"
                    "- Each transition has: id, label, target_state, data_template\n"
                    "- Each transition's target_state matches an actual node NAME in the "
                    "graph\n"
                    "- No duplicate transition labels within a single node\n\n"
                    "TOPOLOGY CHECKS:\n"
                    "- start_node exists and is reachable\n"
                    "- At least one terminal node (node with empty transitions array)\n"
                    "- All non-terminal nodes have at least one transition\n"
                    "- No orphaned nodes (all nodes reachable from start via "
                    "transitions)\n\n"
                    "PROMPT QUALITY CHECKS:\n"
                    "- Every node prompt is substantive (at least 100 characters)\n"
                    "- No prompt contains literal \"[state_machine_output]\" text\n"
                    "- Prompts explain the node's role and expected behavior\n\n"
                    "If ALL checks pass: transition to \"DFA valid\" with a summary of "
                    "the graph.\n"
                    "If ANY check fails: transition to \"Needs fixes\" with a detailed "
                    "list of every issue found, including the specific field/node/value "
                    "that's wrong."
                ),
                "position": {"x": 700, "y": 200},
                "transitions": [
                    {
                        "id": uid(),
                        "label": "DFA valid",
                        "target_state": "Report Result",
                        "data_template": "",
                    },
                    {
                        "id": uid(),
                        "label": "Needs fixes",
                        "target_state": "Generate DFA JSON",
                        "data_template": "",
                    },
                ],
                "persistent_context": True,
                "is_hfa": False,
            },
            report_id: {
                "id": report_id,
                "name": "Report Result",
                "prompt": (
                    "You are the DFA Report Generator. A new DFA graph has been "
                    "created and validated.\n\n"
                    "Read the graph JSON file mentioned in the incoming data from "
                    "data/graphs/. Present a clear summary:\n\n"
                    "1. GRAPH INFO: Name, ID, number of nodes\n"
                    "2. NODE MAP: For each node, show its name, whether it has "
                    "persistent_context, and its transitions (label -> target)\n"
                    "3. FLOW DIAGRAM: Draw an ASCII art diagram showing the node flow\n"
                    "4. USAGE: To use this DFA, go to http://localhost:5050 in a "
                    "browser, select the graph from the dropdown, enter your request in "
                    "the input box, check \"Include original query\" if you want all "
                    "nodes to see the original request, and click Execute.\n\n"
                    "Do NOT modify any files."
                ),
                "position": {"x": 1000, "y": 200},
                "transitions": [],
                "persistent_context": False,
                "is_hfa": False,
            },
        },
    }

    return graph
