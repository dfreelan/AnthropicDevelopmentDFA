// === Claude State Machine Builder - Frontend ===

(() => {
  "use strict";

  // --- State ---
  let graph = null;       // current graph object
  let selectedNodeId = null;
  let executionId = null;
  let pollInterval = null;
  let lastInputData = "";  // remember last input for restart
  let lastIncludeOriginalQuery = false;
  let knownServerId = null;

  // --- Execution tabs ---
  // Map<execId, {id, label, logDiv, execLogUserAtBottom, childXAutoScroll, lastRenderedHistoryLen, status, graphId, bgPollInterval}>
  let execTabs = new Map();
  let activeTabId = null;

  // --- Execution persistence helpers ---
  function saveExecutionId(graphId, execId) {
    localStorage.setItem(`activeExecution_${graphId}`, execId);
  }
  function clearSavedExecutionId(graphId) {
    localStorage.removeItem(`activeExecution_${graphId}`);
  }
  function getSavedExecutionId(graphId) {
    return localStorage.getItem(`activeExecution_${graphId}`);
  }

  // --- Working directory history (server-backed) ---
  const MAX_DIR_HISTORY = 20;
  let workingDirCache = [];

  async function fetchWorkingDirHistory() {
    try {
      workingDirCache = await api("GET", "/api/working-dirs");
    } catch (e) {
      console.error("Failed to fetch working dir history", e);
      workingDirCache = [];
    }
  }

  function getWorkingDirHistory() {
    return workingDirCache;
  }

  async function addToWorkingDirHistory(dir) {
    if (!dir || !dir.trim()) return;
    dir = dir.trim();
    // Optimistic local update
    workingDirCache = [dir, ...workingDirCache.filter(d => d !== dir)].slice(0, MAX_DIR_HISTORY);
    try {
      workingDirCache = await api("POST", "/api/working-dirs", { dir });
    } catch (e) {
      console.error("Failed to save working dir", e);
    }
  }

  async function removeFromWorkingDirHistory(dir) {
    // Optimistic local update
    workingDirCache = workingDirCache.filter(d => d !== dir);
    renderWorkingDirDropdown();
    try {
      workingDirCache = await api("DELETE", "/api/working-dirs", { dir });
    } catch (e) {
      console.error("Failed to remove working dir", e);
    }
  }

  // Canvas pan/zoom
  let panX = 0, panY = 0;
  let zoomLevel = 1;
  let isPanning = false;
  let panStartX, panStartY;

  // Node drag
  let isDragging = false;
  let dragNodeId = null;
  let dragOffsetX, dragOffsetY;

  // Manual double-click tracking (native dblclick fails when DOM is recreated)
  let lastClickNodeId = null;
  let lastClickTime = 0;

  // Auto-save
  let saveTimeout = null;
  let dirCheckTimeout = null;

  // Node library cache
  let nodeLibraryCache = null;
  let nodeLibraryCacheGraphId = null;

  // Git timeline
  let timelineData = null;

  // Image attachments
  let attachedImages = [];  // Array of File objects

  // HFA navigation
  let navPath = [];  // Array of {hfaNodeId, panX, panY, zoomLevel}
  let rootPanX = 0, rootPanY = 0, rootZoomLevel = 1;

  // Bottom panel drag-resize
  let isPanelDragging = false;
  let panelDragStartY = 0;
  let panelDragStartHeight = 0;

  // --- DOM refs ---
  const $ = id => document.getElementById(id);
  const graphNameInput = $("graphName");
  const saveStatus = $("saveStatus");
  const sidePanel = $("sidePanel");
  const panelNoSelection = $("panelNoSelection");
  const panelNodeEdit = $("panelNodeEdit");
  const startNodeSelect = $("startNodeSelect");
  const nodeNameInput = $("nodeNameInput");
  const nodePromptInput = $("nodePromptInput");
  const transitionsList = $("transitionsList");
  const canvasWrapper = $("canvasWrapper");
  const canvas = $("canvas");
  const connectionsSvg = $("connectionsSvg");
  const bottomPanel = $("bottomPanel");
  const bottomPanelHeader = $("bottomPanelHeader");
  const dragHandle = $("dragHandle");
  const btnFullscreen = $("btnFullscreen");
  const execStatus = $("execStatus");
  const execLog = $("execLog");
  const execTabBar = $("execTabBar");
  const feedbackBar = $("feedbackBar");
  const feedbackQuestion = $("feedbackQuestion");
  const feedbackInput = $("feedbackInput");
  const interactiveBar = $("interactiveBar");
  const interactiveInput = $("interactiveInput");
  const loadModal = $("loadModal");
  const graphList = $("graphList");
  const runModal = $("runModal");
  const runInputData = $("runInputData");
  const workingDirInput = $("workingDirInput");
  const workingDirToggle = $("workingDirToggle");
  const workingDirDropdown = $("workingDirDropdown");
  const btnUndo = $("btnUndo");
  const btnRedo = $("btnRedo");
  const snapshotInfo = $("snapshotInfo");

  const breadcrumbBar = $("breadcrumbBar");
  const imageDropZone = $("imageDropZone");
  const imageFileInput = $("imageFileInput");
  const imagePreviews = $("imagePreviews");
  const includeOriginalQueryCheckbox = $("includeOriginalQueryCheckbox");
  const modelOverrideSelect = $("modelOverrideSelect");
  const nodeModelSelect = $("nodeModelSelect");
  const expandPromptBtn = $("expandPromptBtn");
  const promptEditorOverlay = $("promptEditorOverlay");
  const promptEditorTextarea = $("promptEditorTextarea");
  const promptEditorNodeName = $("promptEditorNodeName");
  const promptEditorSave = $("promptEditorSave");
  const promptEditorCancel = $("promptEditorCancel");

  // Socket.IO connection
  const socket = io();

  let execLogUserAtBottom = true;
  let childXAutoScroll = true;
  let lastRenderedHistoryLen = 0;
  let isAutoScrolling = false;

  // --- API helpers ---
  async function api(method, path, body) {
    const opts = { method, headers: { "Content-Type": "application/json" } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    if (!res.ok) throw new Error(`API ${method} ${path} failed: ${res.status}`);
    return res.json();
  }

  // --- HFA navigation helpers ---
  function getActiveGraph() {
    let current = graph;
    for (const entry of navPath) {
      const hfaNode = current.nodes[entry.hfaNodeId];
      current = hfaNode.child_graph;
    }
    return current;
  }

  function navigateInto(hfaNodeId) {
    if (navPath.length === 0) {
      rootPanX = panX;
      rootPanY = panY;
      rootZoomLevel = zoomLevel;
    }
    navPath.push({ hfaNodeId, panX, panY, zoomLevel });
    panX = 0; panY = 0; zoomLevel = 1;
    selectedNodeId = null;
    showNoSelectionPanel();
    renderAll();
    renderBreadcrumbs();
  }

  function navigateToLevel(level) {
    if (level >= navPath.length) return;
    if (level === 0) {
      panX = rootPanX;
      panY = rootPanY;
      zoomLevel = rootZoomLevel;
      navPath = [];
    } else {
      const target = navPath[level - 1];
      panX = target.panX;
      panY = target.panY;
      zoomLevel = target.zoomLevel;
      navPath = navPath.slice(0, level);
    }
    selectedNodeId = null;
    showNoSelectionPanel();
    renderAll();
    renderBreadcrumbs();
  }

  function navigateUp() {
    if (navPath.length === 0) return;
    const popped = navPath.pop();
    if (navPath.length === 0) {
      panX = rootPanX;
      panY = rootPanY;
      zoomLevel = rootZoomLevel;
    } else {
      panX = popped.panX;
      panY = popped.panY;
      zoomLevel = popped.zoomLevel;
    }
    selectedNodeId = null;
    showNoSelectionPanel();
    renderAll();
    renderBreadcrumbs();
  }

  function renderBreadcrumbs() {
    if (navPath.length === 0) {
      breadcrumbBar.style.display = "none";
      return;
    }
    breadcrumbBar.style.display = "flex";
    breadcrumbBar.innerHTML = "";

    // Root crumb
    const rootCrumb = document.createElement("span");
    rootCrumb.className = "breadcrumb-item";
    rootCrumb.textContent = graph.name || "Root";
    rootCrumb.addEventListener("click", () => navigateToLevel(0));
    breadcrumbBar.appendChild(rootCrumb);

    // Walk the path to build crumbs
    let current = graph;
    navPath.forEach((entry, idx) => {
      const sep = document.createElement("span");
      sep.className = "breadcrumb-separator";
      sep.textContent = " \u203A ";
      breadcrumbBar.appendChild(sep);

      const node = current.nodes[entry.hfaNodeId];
      const crumb = document.createElement("span");
      const isLast = idx === navPath.length - 1;
      crumb.className = "breadcrumb-item" + (isLast ? " current" : "");
      crumb.textContent = node.name;
      if (!isLast) {
        crumb.addEventListener("click", () => navigateToLevel(idx + 1));
      }
      breadcrumbBar.appendChild(crumb);

      current = node.child_graph;
    });
  }

  // --- Auto-save ---
  function scheduleSave() {
    if (!graph) return;
    saveStatus.textContent = "Saving...";
    saveStatus.className = "save-status saving";
    clearTimeout(saveTimeout);
    saveTimeout = setTimeout(async () => {
      await api("PUT", `/api/graphs/${graph.id}`, graph);
      saveStatus.textContent = "Saved";
      saveStatus.className = "save-status saved";
      setTimeout(() => { saveStatus.textContent = ""; saveStatus.className = "save-status"; }, 2000);
    }, 500);
  }

  function validateWorkingDir() {
    clearTimeout(dirCheckTimeout);
    const path = workingDirInput.value.trim();
    if (!path) {
      workingDirInput.classList.remove("invalid-dir", "valid-dir");
      return;
    }
    dirCheckTimeout = setTimeout(async () => {
      try {
        const res = await api("GET", `/api/check-directory?path=${encodeURIComponent(path)}`);
        if (workingDirInput.value.trim() !== path) return;
        if (res.exists) {
          workingDirInput.classList.remove("invalid-dir");
          workingDirInput.classList.add("valid-dir");
          addToWorkingDirHistory(path);
        } else {
          workingDirInput.classList.remove("valid-dir");
          workingDirInput.classList.add("invalid-dir");
        }
      } catch (e) {
        workingDirInput.classList.remove("invalid-dir", "valid-dir");
      }
    }, 300);
  }

  // --- Working directory dropdown ---
  function renderWorkingDirDropdown() {
    workingDirDropdown.innerHTML = "";
    const history = getWorkingDirHistory();
    const filter = workingDirInput.value.trim().toLowerCase();
    const filtered = filter ? history.filter(d => d.toLowerCase().includes(filter)) : history;

    if (filtered.length === 0) {
      const empty = document.createElement("div");
      empty.className = "working-dir-dropdown-empty";
      empty.textContent = history.length === 0 ? "No saved directories yet" : "No matching directories";
      workingDirDropdown.appendChild(empty);
      return;
    }

    filtered.forEach(dir => {
      const item = document.createElement("div");
      item.className = "working-dir-dropdown-item";

      const path = document.createElement("span");
      path.className = "dir-path";
      path.textContent = dir;
      item.appendChild(path);

      const remove = document.createElement("span");
      remove.className = "dir-remove";
      remove.textContent = "\u00d7";
      remove.title = "Remove from history";
      remove.addEventListener("mousedown", (e) => {
        e.preventDefault();
        e.stopPropagation();
        removeFromWorkingDirHistory(dir);
        renderWorkingDirDropdown();
      });
      item.appendChild(remove);

      item.addEventListener("mousedown", (e) => {
        e.preventDefault();
        workingDirInput.value = dir;
        graph.working_directory = dir;
        scheduleSave();
        validateWorkingDir();
        hideWorkingDirDropdown();
      });

      workingDirDropdown.appendChild(item);
    });
  }

  function showWorkingDirDropdown() {
    renderWorkingDirDropdown();
    workingDirDropdown.classList.add("active");
  }

  function hideWorkingDirDropdown() {
    workingDirDropdown.classList.remove("active");
  }

  // --- Graph management ---
  async function createNewGraph() {
    graph = await api("POST", "/api/graphs", { name: "Untitled Workflow" });
    localStorage.setItem("lastGraphId", graph.id);
    graphNameInput.value = graph.name;
    workingDirInput.value = graph.working_directory || "";
    modelOverrideSelect.value = "individual";
    validateWorkingDir();
    selectedNodeId = null;
    renderAll();
  }

  async function loadGraph(id) {
    // Clean up any active polling/subscription from previous graph
    if (executionId && pollInterval) {
      stopPolling();
      socket.emit('unsubscribe_execution', { execution_id: executionId });
    }
    clearAllTabs();
    executionId = null;

    graph = await api("GET", `/api/graphs/${id}`);
    localStorage.setItem("lastGraphId", graph.id);
    graphNameInput.value = graph.name;
    workingDirInput.value = graph.working_directory || "";
    modelOverrideSelect.value = graph.model_override || "individual";
    validateWorkingDir();
    selectedNodeId = null;
    navPath = [];
    rootPanX = 0; rootPanY = 0; rootZoomLevel = 1;
    panX = 0; panY = 0; zoomLevel = 1;
    // Backfill model field on nodes created before per-node model selection existed
    let needsSave = false;
    (function backfillModels(g) {
      for (const node of Object.values(g.nodes || {})) {
        if (!node.model) { node.model = "claude-opus-4-6"; needsSave = true; }
        if (node.child_graph) backfillModels(node.child_graph);
      }
    })(graph);
    if (needsSave) scheduleSave();

    renderAll();
    renderBreadcrumbs();
    showNoSelectionPanel();
    fetchTimeline();

    // Try to reconnect to any active execution for this graph
    const reconnected = await tryReconnectExecution();
    if (!reconnected) showRunButtons("idle");
  }

  async function duplicateGraph() {
    if (!graph) return;
    const copy = JSON.parse(JSON.stringify(graph));
    copy.name = graph.name + "_copy";
    const newGraph = await api("POST", "/api/graphs", { name: copy.name });
    newGraph.nodes = copy.nodes;
    newGraph.start_node = copy.start_node;
    newGraph.working_directory = copy.working_directory;
    await api("PUT", `/api/graphs/${newGraph.id}`, newGraph);
    graph = newGraph;
    localStorage.setItem("lastGraphId", graph.id);
    graphNameInput.value = graph.name;
    workingDirInput.value = graph.working_directory || "";
    validateWorkingDir();
    selectedNodeId = null;
    renderAll();
  }

  async function saveWorkspaceAsHfa() {
    if (!graph) return;
    const ag = getActiveGraph();
    const nodeCount = Object.keys(ag.nodes).length;
    if (nodeCount === 0) {
      alert("No nodes in the current workspace to save as HFA.");
      return;
    }

    const hfaName = prompt("Name for the HFA node:", graph.name || "Untitled HFA");
    if (!hfaName) return;

    // Deep copy the current workspace nodes and start_node as the child_graph
    const childGraph = JSON.parse(JSON.stringify({ start_node: ag.start_node, nodes: ag.nodes }));

    // Create a new graph with a single HFA node containing the workspace
    const newGraph = await api("POST", "/api/graphs", { name: hfaName + " (HFA)" });
    const hfaNodeId = crypto.randomUUID();
    const hfaNode = {
      id: hfaNodeId,
      name: hfaName,
      prompt: "",
      position: { x: 300, y: 200 },
      transitions: [],
      persistent_context: false,
      is_hfa: true,
      child_graph: childGraph,
    };
    newGraph.nodes = { [hfaNodeId]: hfaNode };
    newGraph.start_node = hfaNodeId;
    await api("PUT", `/api/graphs/${newGraph.id}`, newGraph);

    // Invalidate node library cache so the new HFA shows up
    nodeLibraryCache = null;
    nodeLibraryCacheGraphId = null;

    alert(`Workspace saved as HFA node "${hfaName}" in graph "${newGraph.name}". It is now available in the Node Library.`);
  }

  async function showLoadModal() {
    const graphs = await api("GET", "/api/graphs");
    graphList.innerHTML = "";
    if (graphs.length === 0) {
      graphList.innerHTML = '<p style="color:#666; text-align:center; padding:20px;">No saved graphs</p>';
    } else {
      graphs.forEach(g => {
        const item = document.createElement("div");
        item.className = "graph-list-item";
        item.innerHTML = `
          <span class="graph-item-name">${esc(g.name)}</span>
          <button class="graph-item-delete" data-id="${g.id}" title="Delete">&times;</button>
        `;
        item.addEventListener("click", (e) => {
          if (e.target.classList.contains("graph-item-delete")) return;
          loadGraph(g.id);
          loadModal.classList.remove("active");
        });
        item.querySelector(".graph-item-delete").addEventListener("click", async (e) => {
          e.stopPropagation();
          if (confirm(`Delete "${g.name}"?`)) {
            await api("DELETE", `/api/graphs/${g.id}`);
            showLoadModal();
          }
        });
        graphList.appendChild(item);
      });
    }
    loadModal.classList.add("active");
  }

  // --- Node CRUD ---
  function addNode(name, x, y, isHfa) {
    const ag = getActiveGraph();
    const id = crypto.randomUUID();
    const nodeObj = {
      id,
      name: name || `node_${Object.keys(ag.nodes).length + 1}`,
      prompt: "",
      position: { x: x || 200 + Math.random() * 200, y: y || 150 + Math.random() * 200 },
      transitions: [],
      persistent_context: false,
      model: "claude-opus-4-6",
    };
    if (isHfa) {
      nodeObj.is_hfa = true;
      nodeObj.child_graph = { start_node: null, nodes: {} };
    }
    ag.nodes[id] = nodeObj;
    if (!ag.start_node) {
      ag.start_node = id;
    }
    scheduleSave();
    renderAll();
    selectNode(id);
    return id;
  }

  function deleteNode(id) {
    const ag = getActiveGraph();
    if (!confirm(`Delete node "${ag.nodes[id]?.name}"?`)) return;
    delete ag.nodes[id];
    Object.values(ag.nodes).forEach(n => {
      n.transitions = n.transitions.filter(t => {
        const target = resolveTargetNode(t.target_state);
        return target !== id;
      });
    });
    if (ag.start_node === id) {
      const keys = Object.keys(ag.nodes);
      ag.start_node = keys.length > 0 ? keys[0] : null;
    }
    if (selectedNodeId === id) {
      selectedNodeId = null;
      showNoSelectionPanel();
    }
    scheduleSave();
    renderAll();
  }

  function resolveTargetNode(nameOrId) {
    const ag = getActiveGraph();
    if (ag.nodes[nameOrId]) return nameOrId;
    for (const [nid, n] of Object.entries(ag.nodes)) {
      if (n.name === nameOrId) return nid;
    }
    return null;
  }

  function findNodeByName(name) {
    const ag = getActiveGraph();
    for (const [nid, n] of Object.entries(ag.nodes)) {
      if (n.name === name) return nid;
    }
    return null;
  }

  // --- Selection ---
  function selectNode(id) {
    if (selectedNodeId === id) return;
    selectedNodeId = id;
    renderAll();
    showNodeEditPanel(id);
  }

  function deselectNode() {
    selectedNodeId = null;
    renderAll();
    showNoSelectionPanel();
  }

  // --- Rendering ---
  function renderAll() {
    renderNodes();
    renderConnections();
    renderStartNodeSelect();
    updateCanvasTransform();
    renderBreadcrumbs();
  }

  function updateCanvasTransform() {
    canvas.style.transform = `translate(${panX}px, ${panY}px) scale(${zoomLevel})`;
    canvas.style.transformOrigin = '0 0';
  }

  function renderNodes() {
    canvas.querySelectorAll(".node").forEach(el => el.remove());
    if (!graph) return;
    const ag = getActiveGraph();
    Object.values(ag.nodes).forEach(node => {
      const div = document.createElement("div");
      div.className = "node";
      div.dataset.nodeId = node.id;
      if (node.id === selectedNodeId) div.classList.add("selected");
      if (node.id === ag.start_node) div.classList.add("start-node");
      if (node.is_hfa) div.classList.add("hfa-node");
      div.style.left = node.position.x + "px";
      div.style.top = node.position.y + "px";

      const nameSpan = document.createElement("span");
      nameSpan.className = "node-name";
      nameSpan.textContent = node.name;
      div.appendChild(nameSpan);

      if (node.is_hfa) {
        const badge = document.createElement("span");
        badge.className = "hfa-badge";
        const childCount = Object.keys(node.child_graph?.nodes || {}).length;
        badge.textContent = childCount > 0 ? `HFA (${childCount})` : "HFA";
        div.appendChild(badge);

        const hint = document.createElement("div");
        hint.className = "hfa-enter-hint";
        hint.textContent = "double-click to enter";
        div.appendChild(hint);
      }

      if (node.model === "claude-sonnet-4-6") {
        const modelBadge = document.createElement("span");
        modelBadge.className = "model-badge sonnet";
        modelBadge.textContent = "Sonnet";
        div.appendChild(modelBadge);
      } else if (node.model === "claude-haiku-4-5-20251001") {
        const modelBadge = document.createElement("span");
        modelBadge.className = "model-badge haiku";
        modelBadge.textContent = "Haiku";
        div.appendChild(modelBadge);
      }

      div.addEventListener("mousedown", (e) => {
        if (e.button !== 0) return;
        e.stopPropagation();
        isDragging = false;
        dragNodeId = node.id;
        const rect = div.getBoundingClientRect();
        dragOffsetX = (e.clientX - rect.left) / zoomLevel;
        dragOffsetY = (e.clientY - rect.top) / zoomLevel;

        const onMouseMove = (e2) => {
          isDragging = true;
          const wrapperRect = canvasWrapper.getBoundingClientRect();
          const newX = (e2.clientX - wrapperRect.left - panX) / zoomLevel - dragOffsetX;
          const newY = (e2.clientY - wrapperRect.top - panY) / zoomLevel - dragOffsetY;
          node.position.x = newX;
          node.position.y = newY;
          div.style.left = node.position.x + "px";
          div.style.top = node.position.y + "px";
          renderConnections();
        };

        const onMouseUp = () => {
          document.removeEventListener("mousemove", onMouseMove);
          document.removeEventListener("mouseup", onMouseUp);
          if (isDragging) {
            scheduleSave();
          } else {
            const now = Date.now();
            if (node.id === lastClickNodeId && (now - lastClickTime) < 350) {
              lastClickNodeId = null;
              lastClickTime = 0;
              if (node.is_hfa) {
                navigateInto(node.id);
              }
            } else {
              lastClickNodeId = node.id;
              lastClickTime = now;
              selectNode(node.id);
            }
          }
          isDragging = false;
          dragNodeId = null;
        };

        document.addEventListener("mousemove", onMouseMove);
        document.addEventListener("mouseup", onMouseUp);
      });

      div.addEventListener("dblclick", (e) => {
        if (node.is_hfa) {
          e.stopPropagation();
          navigateInto(node.id);
        }
      });

      canvas.appendChild(div);
    });
  }

  function getRectEdgePoint(cx, cy, width, height, angle) {
    // Given center (cx,cy) and rectangle dimensions, find where a ray at `angle` exits the rect
    const halfW = width / 2;
    const halfH = height / 2;
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    // Time to hit vertical edge vs horizontal edge
    const tX = cos !== 0 ? halfW / Math.abs(cos) : Infinity;
    const tY = sin !== 0 ? halfH / Math.abs(sin) : Infinity;
    const t = Math.min(tX, tY);
    return { x: cx + cos * t, y: cy + sin * t };
  }

  function renderConnections() {
    connectionsSvg.querySelectorAll("path.conn, text.conn-label").forEach(el => el.remove());
    if (!graph) return;
    const ag = getActiveGraph();

    // --- Phase 1: Detect bidirectional edge pairs ---
    const directedEdges = new Set();
    Object.values(ag.nodes).forEach(srcNode => {
      srcNode.transitions.forEach(tr => {
        const targetId = resolveTargetNode(tr.target_state);
        if (targetId && targetId !== srcNode.id) {
          directedEdges.add(srcNode.id + "->" + targetId);
        }
      });
    });

    const bidirectionalPairs = new Set();
    directedEdges.forEach(edge => {
      const parts = edge.split("->");
      const reverse = parts[1] + "->" + parts[0];
      if (directedEdges.has(reverse)) {
        bidirectionalPairs.add(edge);
        bidirectionalPairs.add(reverse);
      }
    });

    // --- Phase 2: Draw edges ---
    Object.values(ag.nodes).forEach(srcNode => {
      const srcEl = canvas.querySelector(`[data-node-id="${srcNode.id}"]`);
      if (!srcEl) return;

      srcNode.transitions.forEach(tr => {
        const targetId = resolveTargetNode(tr.target_state);
        if (!targetId) return;
        const tgtEl = canvas.querySelector(`[data-node-id="${targetId}"]`);
        if (!tgtEl) return;

        const sx = srcNode.position.x + srcEl.offsetWidth / 2;
        const sy = srcNode.position.y + srcEl.offsetHeight / 2;
        const targetNode = ag.nodes[targetId];
        const tx = targetNode.position.x + tgtEl.offsetWidth / 2;
        const ty = targetNode.position.y + tgtEl.offsetHeight / 2;

        // Self-loop (unchanged)
        if (srcNode.id === targetId) {
          const loopPath = `M${sx},${sy - 20} C${sx - 60},${sy - 80} ${sx + 60},${sy - 80} ${sx},${sy - 20}`;
          const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
          path.setAttribute("d", loopPath);
          path.setAttribute("marker-end", "url(#arrowhead)");
          path.classList.add("conn");
          connectionsSvg.appendChild(path);

          const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
          label.setAttribute("x", sx);
          label.setAttribute("y", sy - 85);
          label.setAttribute("text-anchor", "middle");
          label.classList.add("conn-label");
          label.textContent = tr.label;
          connectionsSvg.appendChild(label);
          return;
        }

        // Determine if this edge is part of a bidirectional pair
        const edgeKey = srcNode.id + "->" + targetId;
        const isBidirectional = bidirectionalPairs.has(edgeKey);

        const dx = tx - sx;
        const dy = ty - sy;
        const dist = Math.sqrt(dx * dx + dy * dy);

        // For bidirectional edges, compute perpendicular offset
        let nx = 0;
        let ny = 0;
        if (isBidirectional && dist > 0) {
          const curvature = Math.min(dist * 0.1, 40);
          const canonDx = srcNode.id < targetId ? dx : -dx;
          const canonDy = srcNode.id < targetId ? dy : -dy;
          const sign = srcNode.id < targetId ? 1 : -1;
          nx = -canonDy / dist * curvature * sign;
          ny = canonDx / dist * curvature * sign;
        }

        // Compute "effective" targets: offset centers by perpendicular amount.
        // This makes the departure/arrival angles incorporate the curve offset,
        // so the Bezier tangent at endpoints points in the correct direction.
        const effTx = tx + nx;
        const effTy = ty + ny;
        const effSx = sx + nx;
        const effSy = sy + ny;

        // Departure angle: from source center toward effective target
        const departAngle = Math.atan2(effTy - sy, effTx - sx);
        const startPt = getRectEdgePoint(sx, sy, srcEl.offsetWidth, srcEl.offsetHeight, departAngle);
        const startX = startPt.x;
        const startY = startPt.y;

        // Arrival angle: from target center toward effective source (reversed)
        const arrivalAngle = Math.atan2(effSy - ty, effSx - tx);
        const endPt = getRectEdgePoint(tx, ty, tgtEl.offsetWidth, tgtEl.offsetHeight, arrivalAngle);
        const endX = endPt.x;
        const endY = endPt.y;

        // Place control points along the departure/arrival directions.
        // cpDist controls curve shape but does NOT affect tangent accuracy.
        const cpDist = Math.min(dist * 0.25, 60);
        const cx1 = startX + Math.cos(departAngle) * cpDist;
        const cy1 = startY + Math.sin(departAngle) * cpDist;
        const cx2 = endX - Math.cos(departAngle) * cpDist;
        const cy2 = endY - Math.sin(departAngle) * cpDist;

        const d = `M${startX},${startY} C${cx1},${cy1} ${cx2},${cy2} ${endX},${endY}`;
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", d);
        path.setAttribute("marker-end", "url(#arrowhead)");
        path.classList.add("conn");
        connectionsSvg.appendChild(path);

        const mx = (sx + tx) / 2 + nx;
        const my = (sy + ty) / 2 + ny;
        const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
        label.setAttribute("x", mx);
        label.setAttribute("y", my - 6);
        label.setAttribute("text-anchor", "middle");
        label.classList.add("conn-label");
        label.textContent = tr.label;
        connectionsSvg.appendChild(label);
      });
    });
  }

  function renderStartNodeSelect() {
    startNodeSelect.innerHTML = '<option value="">-- none --</option>';
    if (!graph) return;
    const ag = getActiveGraph();
    Object.values(ag.nodes).forEach(n => {
      const opt = document.createElement("option");
      opt.value = n.id;
      opt.textContent = n.name;
      if (n.id === ag.start_node) opt.selected = true;
      startNodeSelect.appendChild(opt);
    });
  }

  // --- Node Library ---
  async function fetchNodeLibrary() {
    try {
      nodeLibraryCache = await api("GET", `/api/nodes/library?exclude=${graph.id}`);
      nodeLibraryCacheGraphId = graph.id;
    } catch (e) {
      console.error("Failed to fetch node library:", e);
      nodeLibraryCache = null;
    }
    renderNodeLibrary("");
  }

  function renderNodeLibrary(filter) {
    const container = $("nodeLibraryResults");
    if (!container) return;
    container.innerHTML = "";
    if (!nodeLibraryCache) {
      container.innerHTML = '<p class="hint">Could not load node library.</p>';
      return;
    }
    if (nodeLibraryCache.length === 0) {
      container.innerHTML = '<p class="hint">No saved nodes yet.</p>';
      return;
    }
    const lf = filter.toLowerCase();
    const matches = lf
      ? nodeLibraryCache.filter(n => n.name.toLowerCase().includes(lf) || n.source_graph_name.toLowerCase().includes(lf))
      : nodeLibraryCache;
    if (matches.length === 0) {
      container.innerHTML = '<p class="hint">No matching nodes.</p>';
      return;
    }
    matches.forEach(n => {
      const item = document.createElement("div");
      item.className = "library-node-item" + (n.is_current_graph ? " library-node-current" : "");
      const hfaTag = n.is_hfa ? ' <span class="hfa-tag">(HFA)</span>' : '';
      item.innerHTML = `<div class="library-node-name">${esc(n.name)}${hfaTag}${n.is_current_graph ? ' <span class="current-tag">(current)</span>' : ''}</div><div class="library-node-source">${esc(n.source_graph_name)}</div>`;
      item.addEventListener("click", () => importLibraryNode(n.name, n.prompt, n.is_hfa, n.child_graph));
      container.appendChild(item);
    });
  }

  function importLibraryNode(name, prompt, isHfa, childGraph) {
    if (!graph) return;
    const id = addNode(name, null, null, isHfa);
    const ag = getActiveGraph();
    ag.nodes[id].prompt = prompt;
    if (isHfa && childGraph) {
      // Deep copy the child_graph and reassign IDs to avoid conflicts
      const copied = JSON.parse(JSON.stringify(childGraph));
      const idMap = {};
      const newNodes = {};
      for (const [oldId, node] of Object.entries(copied.nodes || {})) {
        const newId = crypto.randomUUID();
        idMap[oldId] = newId;
        node.id = newId;
        newNodes[newId] = node;
      }
      // Update start_node reference
      if (copied.start_node && idMap[copied.start_node]) {
        copied.start_node = idMap[copied.start_node];
      }
      // Update transition target references (by ID)
      for (const node of Object.values(newNodes)) {
        if (node.transitions) {
          node.transitions.forEach(t => {
            if (idMap[t.target_state]) {
              t.target_state = idMap[t.target_state];
            }
            t.id = crypto.randomUUID();
          });
        }
      }
      copied.nodes = newNodes;
      ag.nodes[id].child_graph = copied;
    }
    scheduleSave();
    selectNode(id);
  }

  // --- Side Panel ---
  function showNoSelectionPanel() {
    panelNoSelection.style.display = "";
    panelNodeEdit.style.display = "none";
    if (graph) {
      if (!nodeLibraryCache || nodeLibraryCacheGraphId !== graph.id) {
        fetchNodeLibrary().catch(e => console.error("Node library fetch error:", e));
      } else {
        const searchEl = $("nodeLibrarySearch");
        renderNodeLibrary(searchEl ? searchEl.value : "");
      }
    }
  }

  function showNodeEditPanel(nodeId) {
    const ag = getActiveGraph();
    const node = ag.nodes[nodeId];
    if (!node) return;

    panelNoSelection.style.display = "none";
    panelNodeEdit.style.display = "";

    nodeNameInput.value = node.name;
    nodePromptInput.value = node.prompt;

    const pcCheckbox = $("persistentContextCheckbox");
    pcCheckbox.checked = !!node.persistent_context;
    pcCheckbox.onchange = () => { node.persistent_context = pcCheckbox.checked; scheduleSave(); };

    if (!node.model) { node.model = "claude-opus-4-6"; scheduleSave(); }
    nodeModelSelect.value = node.model;

    // HFA controls
    const hfaControls = $("hfaControls");
    const hfaCheckbox = $("hfaCheckbox");
    const btnEnterHfa = $("btnEnterHfa");
    const hfaForwardLabel = $("hfaForwardIncomingLabel");
    const hfaForwardCheckbox = $("hfaForwardIncomingCheckbox");
    hfaControls.style.display = "";
    hfaCheckbox.checked = !!node.is_hfa;
    btnEnterHfa.style.display = node.is_hfa ? "" : "none";
    hfaForwardLabel.style.display = node.is_hfa ? "" : "none";
    hfaForwardCheckbox.checked = !!node.forward_incoming_to_children;

    hfaForwardCheckbox.onchange = () => {
      node.forward_incoming_to_children = hfaForwardCheckbox.checked;
      scheduleSave();
    };

    hfaCheckbox.onchange = () => {
      if (hfaCheckbox.checked) {
        node.is_hfa = true;
        if (!node.child_graph) {
          node.child_graph = { start_node: null, nodes: {} };
        }
      } else {
        if (node.child_graph && Object.keys(node.child_graph.nodes).length > 0) {
          if (!confirm("This will discard all nodes inside this HFA. Continue?")) {
            hfaCheckbox.checked = true;
            return;
          }
        }
        node.is_hfa = false;
        delete node.child_graph;
        delete node.forward_incoming_to_children;
      }
      btnEnterHfa.style.display = node.is_hfa ? "" : "none";
      hfaForwardLabel.style.display = node.is_hfa ? "" : "none";
      scheduleSave();
      renderAll();
    };

    btnEnterHfa.onclick = () => navigateInto(nodeId);

    renderTransitionsEditor(node);
  }

  function renderTransitionsEditor(node) {
    transitionsList.innerHTML = "";

    node.transitions.forEach((tr, idx) => {
      const item = document.createElement("div");
      item.className = "transition-item";

      item.innerHTML = `
        <button class="remove-transition" data-idx="${idx}">&times;</button>
        <div class="form-group">
          <label>Label</label>
          <input type="text" class="tr-label" value="${esc(tr.label)}" placeholder="e.g. success">
        </div>
        <div class="form-group autocomplete-wrapper">
          <label>Target State</label>
          <input type="text" class="tr-target" value="${esc(tr.target_state)}" placeholder="Node name">
          <div class="autocomplete-list"></div>
        </div>
        <div class="form-group">
          <label>Data Template (XML)</label>
          <textarea class="tr-data" rows="3" placeholder="<data>...</data>">${esc(tr.data_template || "")}</textarea>
        </div>
      `;

      item.querySelector(".remove-transition").addEventListener("click", () => {
        node.transitions.splice(idx, 1);
        scheduleSave();
        renderTransitionsEditor(node);
        renderConnections();
      });

      item.querySelector(".tr-label").addEventListener("input", (e) => {
        tr.label = e.target.value;
        scheduleSave();
        renderConnections();
      });

      const targetInput = item.querySelector(".tr-target");
      const acList = item.querySelector(".autocomplete-list");

      targetInput.addEventListener("input", (e) => {
        tr.target_state = e.target.value;
        scheduleSave();
        renderConnections();
        showAutocomplete(acList, e.target.value, targetInput, tr);
      });

      targetInput.addEventListener("focus", () => {
        showAutocomplete(acList, targetInput.value, targetInput, tr);
      });

      targetInput.addEventListener("blur", () => {
        setTimeout(() => {
          acList.classList.remove("active");
          autoCreateNodeIfNeeded(tr.target_state, node);
        }, 200);
      });

      item.querySelector(".tr-data").addEventListener("input", (e) => {
        tr.data_template = e.target.value;
        scheduleSave();
      });

      transitionsList.appendChild(item);
    });
  }

  function showAutocomplete(acList, query, input, tr) {
    acList.innerHTML = "";
    if (!query) { acList.classList.remove("active"); return; }
    const ag = getActiveGraph();
    const matches = Object.values(ag.nodes)
      .filter(n => n.name.toLowerCase().includes(query.toLowerCase()) && n.id !== selectedNodeId)
      .slice(0, 5);

    if (matches.length === 0) {
      acList.classList.remove("active");
      return;
    }

    matches.forEach(n => {
      const item = document.createElement("div");
      item.className = "ac-item";
      item.textContent = n.name;
      item.addEventListener("mousedown", (e) => {
        e.preventDefault();
        input.value = n.name;
        tr.target_state = n.name;
        acList.classList.remove("active");
        scheduleSave();
        renderConnections();
      });
      acList.appendChild(item);
    });
    acList.classList.add("active");
  }

  function autoCreateNodeIfNeeded(targetName, sourceNode) {
    if (!targetName || targetName.trim() === "") return;
    const existing = findNodeByName(targetName.trim());
    if (existing) return;

    const offsetX = 250;
    const offsetY = 80 * (sourceNode.transitions.length - 1);
    addNode(targetName.trim(), sourceNode.position.x + offsetX, sourceNode.position.y + offsetY);
  }

  // --- Canvas panning ---
  canvasWrapper.addEventListener("mousedown", (e) => {
    if (e.target === canvasWrapper || e.target.classList.contains("grid-bg") || e.target === canvas || e.target === connectionsSvg) {
      isPanning = true;
      panStartX = e.clientX - panX;
      panStartY = e.clientY - panY;
      deselectNode();
    }
  });

  document.addEventListener("mousemove", (e) => {
    if (isPanning) {
      panX = e.clientX - panStartX;
      panY = e.clientY - panStartY;
      updateCanvasTransform();
    }
  });

  document.addEventListener("mouseup", () => {
    isPanning = false;
  });

  // --- Canvas zoom ---
  canvasWrapper.addEventListener("wheel", (e) => {
    e.preventDefault();
    const zoomSpeed = 0.001;
    const minZoom = 0.2;
    const maxZoom = 3.0;

    const oldZoom = zoomLevel;
    zoomLevel *= 1 - e.deltaY * zoomSpeed;
    zoomLevel = Math.min(maxZoom, Math.max(minZoom, zoomLevel));

    // Zoom toward cursor position
    const wrapperRect = canvasWrapper.getBoundingClientRect();
    const cursorX = e.clientX - wrapperRect.left;
    const cursorY = e.clientY - wrapperRect.top;

    // Adjust pan so the point under the cursor stays fixed
    panX = cursorX - (cursorX - panX) * (zoomLevel / oldZoom);
    panY = cursorY - (cursorY - panY) * (zoomLevel / oldZoom);

    updateCanvasTransform();
  }, { passive: false });

  // --- Keyboard shortcuts ---
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;

    if (e.key === "Delete" || e.key === "Backspace") {
      if (selectedNodeId && getActiveGraph().nodes[selectedNodeId]) {
        deleteNode(selectedNodeId);
      }
    }
    if (e.key === "Escape" && !promptEditorOverlay.classList.contains("active")) {
      if (navPath.length > 0) {
        navigateUp();
      } else {
        deselectNode();
      }
    }
  });

  // --- Toolbar events ---
  $("btnNewNode").addEventListener("click", () => {
    if (!graph) return;
    addNode();
  });

  $("btnNewHfaNode").addEventListener("click", () => {
    if (!graph) return;
    addNode(null, null, null, true);
  });

  $("btnNew").addEventListener("click", createNewGraph);
  $("btnDuplicate").addEventListener("click", duplicateGraph);
  $("btnSaveAsHfa").addEventListener("click", saveWorkspaceAsHfa);

  $("btnSave").addEventListener("click", async () => {
    if (!graph) return;
    await api("PUT", `/api/graphs/${graph.id}`, graph);
    saveStatus.textContent = "Saved";
    saveStatus.className = "save-status saved";
    setTimeout(() => { saveStatus.textContent = ""; saveStatus.className = "save-status"; }, 2000);
  });

  $("btnLoad").addEventListener("click", showLoadModal);

  $("btnCloseModal").addEventListener("click", () => {
    loadModal.classList.remove("active");
  });

  loadModal.addEventListener("click", (e) => {
    if (e.target === loadModal) loadModal.classList.remove("active");
  });

  graphNameInput.addEventListener("input", () => {
    if (!graph) return;
    graph.name = graphNameInput.value;
    scheduleSave();
  });

  workingDirInput.addEventListener("input", () => {
    if (!graph) return;
    graph.working_directory = workingDirInput.value;
    scheduleSave();
    validateWorkingDir();
    if (workingDirDropdown.classList.contains("active")) {
      renderWorkingDirDropdown();
    }
  });

  workingDirToggle.addEventListener("click", () => {
    if (workingDirDropdown.classList.contains("active")) {
      hideWorkingDirDropdown();
    } else {
      showWorkingDirDropdown();
    }
    workingDirInput.focus();
  });

  workingDirInput.addEventListener("focus", () => {
    if (getWorkingDirHistory().length > 0) {
      showWorkingDirDropdown();
    }
  });

  workingDirInput.addEventListener("blur", () => {
    setTimeout(hideWorkingDirDropdown, 200);
  });

  startNodeSelect.addEventListener("change", () => {
    if (!graph) return;
    const ag = getActiveGraph();
    ag.start_node = startNodeSelect.value || null;
    scheduleSave();
    renderNodes();
  });

  modelOverrideSelect.addEventListener("change", () => {
    if (!graph) return;
    graph.model_override = modelOverrideSelect.value;
    scheduleSave();
  });

  // --- Node edit panel events ---
  nodeNameInput.addEventListener("input", () => {
    const ag = getActiveGraph();
    if (!selectedNodeId || !ag.nodes[selectedNodeId]) return;
    const oldName = ag.nodes[selectedNodeId].name;
    const newName = nodeNameInput.value;
    ag.nodes[selectedNodeId].name = newName;

    Object.values(ag.nodes).forEach(n => {
      n.transitions.forEach(t => {
        if (t.target_state === oldName) {
          t.target_state = newName;
        }
      });
    });

    scheduleSave();
    renderNodes();
    renderConnections();
    renderStartNodeSelect();
  });

  nodePromptInput.addEventListener("input", () => {
    const ag = getActiveGraph();
    if (!selectedNodeId || !ag.nodes[selectedNodeId]) return;
    ag.nodes[selectedNodeId].prompt = nodePromptInput.value;
    scheduleSave();
  });

  // --- Prompt Editor Modal ---
  function openPromptEditor() {
    const ag = getActiveGraph();
    if (!selectedNodeId || !ag.nodes[selectedNodeId]) return;
    const node = ag.nodes[selectedNodeId];
    promptEditorNodeName.textContent = node.name;
    promptEditorTextarea.value = nodePromptInput.value;
    promptEditorOverlay.classList.add("active");
    promptEditorTextarea.focus();
    promptEditorTextarea.setSelectionRange(promptEditorTextarea.value.length, promptEditorTextarea.value.length);
  }

  function closePromptEditor(save) {
    if (save) {
      const ag = getActiveGraph();
      if (selectedNodeId && ag.nodes[selectedNodeId]) {
        ag.nodes[selectedNodeId].prompt = promptEditorTextarea.value;
        nodePromptInput.value = promptEditorTextarea.value;
        scheduleSave();
      }
    }
    promptEditorOverlay.classList.remove("active");
  }

  expandPromptBtn.addEventListener("click", openPromptEditor);
  promptEditorSave.addEventListener("click", () => closePromptEditor(true));
  promptEditorCancel.addEventListener("click", () => closePromptEditor(false));

  promptEditorOverlay.addEventListener("click", (e) => {
    if (e.target === promptEditorOverlay) closePromptEditor(false);
  });

  promptEditorTextarea.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      closePromptEditor(false);
    }
    if (e.key === "Enter" && e.ctrlKey) {
      e.preventDefault();
      closePromptEditor(true);
    }
  });

  nodeModelSelect.addEventListener("change", () => {
    const ag = getActiveGraph();
    if (!selectedNodeId || !ag.nodes[selectedNodeId]) return;
    ag.nodes[selectedNodeId].model = nodeModelSelect.value;
    scheduleSave();
    renderNodes();
  });

  const nodeLibrarySearch = $("nodeLibrarySearch");
  if (nodeLibrarySearch) {
    nodeLibrarySearch.addEventListener("input", (e) => {
      renderNodeLibrary(e.target.value);
    });
  }

  $("btnAddTransition").addEventListener("click", () => {
    const ag = getActiveGraph();
    if (!selectedNodeId || !ag.nodes[selectedNodeId]) return;
    ag.nodes[selectedNodeId].transitions.push({
      id: crypto.randomUUID(),
      target_state: "",
      label: "",
      data_template: "",
    });
    scheduleSave();
    renderTransitionsEditor(ag.nodes[selectedNodeId]);
  });

  $("btnSetStart").addEventListener("click", () => {
    if (!selectedNodeId) return;
    const ag = getActiveGraph();
    ag.start_node = selectedNodeId;
    scheduleSave();
    renderAll();
  });

  $("btnDeleteNode").addEventListener("click", () => {
    if (!selectedNodeId) return;
    deleteNode(selectedNodeId);
  });

  // --- Git Timeline ---
  async function fetchTimeline() {
    if (!graph || !graph.id) return;
    try {
      timelineData = await api("GET", `/api/graphs/${graph.id}/timeline`);
      btnUndo.disabled = !timelineData.can_undo;
      btnRedo.disabled = !timelineData.can_redo;
      if (timelineData.total > 0) {
        snapshotInfo.textContent = `Snapshot ${timelineData.current_index + 1}/${timelineData.total}`;
      } else {
        snapshotInfo.textContent = "";
      }
    } catch (e) {
      timelineData = null;
      btnUndo.disabled = true;
      btnRedo.disabled = true;
      snapshotInfo.textContent = "";
    }
  }

  function appendSystemMessage(text) {
    const targetLog = (activeTabId && execTabs.has(activeTabId)) ? execTabs.get(activeTabId).logDiv : execLog;
    const div = document.createElement("div");
    div.className = "log-entry";
    const time = new Date().toLocaleTimeString();
    div.innerHTML = `<span class="log-system">[${time}] ${esc(text)}</span>`;
    targetLog.appendChild(div);
    if (execLogUserAtBottom) {
      const content = bottomPanel.querySelector(".bottom-panel-content");
      content.scrollTop = content.scrollHeight;
    }
  }

  btnUndo.addEventListener("click", async () => {
    if (!graph) return;
    btnUndo.disabled = true;
    try {
      const result = await api("POST", `/api/graphs/${graph.id}/undo`);
      if (result.error) {
        alert(result.error);
      } else {
        appendSystemMessage(`[git] Undid to: ${result.snapshot.label}`);
      }
    } catch (e) {
      alert("Undo failed: " + e.message);
    }
    await fetchTimeline();
  });

  btnRedo.addEventListener("click", async () => {
    if (!graph) return;
    btnRedo.disabled = true;
    try {
      const result = await api("POST", `/api/graphs/${graph.id}/redo`);
      if (result.error) {
        alert(result.error);
      } else {
        appendSystemMessage(`[git] Redid to: ${result.snapshot.label}`);
      }
    } catch (e) {
      alert("Redo failed: " + e.message);
    }
    await fetchTimeline();
  });

  // --- Image Attachments ---
  function addImageFiles(files) {
    for (const file of files) {
      if (!file.type.startsWith("image/")) continue;
      // Deduplicate by name+size
      const isDupe = attachedImages.some(f => f.name === file.name && f.size === file.size);
      if (!isDupe) attachedImages.push(file);
    }
    renderImagePreviews();
  }

  function removeImage(index) {
    const removed = attachedImages.splice(index, 1);
    if (removed.length && removed[0]._previewUrl) {
      URL.revokeObjectURL(removed[0]._previewUrl);
    }
    renderImagePreviews();
  }

  function renderImagePreviews() {
    imagePreviews.innerHTML = "";
    if (attachedImages.length === 0) {
      imagePreviews.style.display = "none";
      return;
    }
    imagePreviews.style.display = "flex";
    attachedImages.forEach((file, idx) => {
      const wrapper = document.createElement("div");
      wrapper.className = "image-preview";

      const img = document.createElement("img");
      if (!file._previewUrl) file._previewUrl = URL.createObjectURL(file);
      img.src = file._previewUrl;
      img.alt = file.name;
      img.title = file.name;

      const removeBtn = document.createElement("button");
      removeBtn.className = "remove-btn";
      removeBtn.textContent = "\u00d7";
      removeBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        removeImage(idx);
      });

      wrapper.appendChild(img);
      wrapper.appendChild(removeBtn);
      imagePreviews.appendChild(wrapper);
    });
  }

  function clearAttachedImages() {
    attachedImages.forEach(f => {
      if (f._previewUrl) URL.revokeObjectURL(f._previewUrl);
    });
    attachedImages = [];
    renderImagePreviews();
  }

  // Drop zone events
  imageDropZone.addEventListener("click", () => imageFileInput.click());

  imageDropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    imageDropZone.classList.add("drag-over");
  });

  imageDropZone.addEventListener("dragleave", () => {
    imageDropZone.classList.remove("drag-over");
  });

  imageDropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    imageDropZone.classList.remove("drag-over");
    if (e.dataTransfer.files.length) addImageFiles(e.dataTransfer.files);
  });

  imageFileInput.addEventListener("change", () => {
    if (imageFileInput.files.length) addImageFiles(imageFileInput.files);
    imageFileInput.value = "";
  });

  // Paste handler on the run modal
  runModal.addEventListener("paste", (e) => {
    const files = e.clipboardData?.files;
    if (files && files.length) {
      const imageFiles = Array.from(files).filter(f => f.type.startsWith("image/"));
      if (imageFiles.length) {
        addImageFiles(imageFiles);
      }
    }
  });

  // --- Execution Tab Management ---
  function createTab(execId, graphId, inputData) {
    // Create a new logDiv for this tab
    const logDiv = document.createElement("div");
    logDiv.className = "exec-log";
    logDiv.dataset.execId = execId;
    // Insert logDiv before the interactiveBar inside bottom-panel-content
    const contentDiv = bottomPanel.querySelector(".bottom-panel-content");
    contentDiv.insertBefore(logDiv, interactiveBar);

    // Generate initial label from input data
    let initialLabel = "New Run";
    if (inputData && inputData.trim()) {
      const words = inputData.trim().split(/\s+/).filter(w => w.length > 2).slice(0, 2);
      if (words.length > 0) initialLabel = words.join(' ').substring(0, 30);
    }

    const tab = {
      id: execId,
      label: initialLabel,
      logDiv,
      execLogUserAtBottom: true,
      childXAutoScroll: true,
      lastRenderedHistoryLen: 0,
      status: 'running',
      graphId,
      bgPollInterval: null,
    };
    execTabs.set(execId, tab);

    // Add capture-phase scroll listener for live output on this tab's logDiv
    logDiv.addEventListener("scroll", (e) => {
      if (isAutoScrolling) return;
      const pre = e.target;
      if (pre.tagName !== "PRE") return;
      if (!pre.closest(".live-output-entry")) return;
      const thisTab = execTabs.get(execId);
      if (thisTab) thisTab.childXAutoScroll = (pre.scrollHeight - pre.scrollTop - pre.clientHeight) < 50;
      if (execId === activeTabId) childXAutoScroll = thisTab ? thisTab.childXAutoScroll : true;
    }, true);

    logDiv.addEventListener("toggle", (e) => {
      if (!e.target.closest || !e.target.closest(".log-entry")) return;
      const thisTab = execTabs.get(execId);
      if (thisTab) {
        thisTab.execLogUserAtBottom = false;
        thisTab.childXAutoScroll = false;
      }
      if (execId === activeTabId) {
        execLogUserAtBottom = false;
        childXAutoScroll = false;
      }
    }, true);

    renderTabBar();
    switchToTab(execId);

    // Async fetch a smart label from the backend
    setTimeout(() => {
      fetch(`/api/execution/${execId}/label`)
        .then(r => r.json())
        .then(data => {
          if (data.label) {
            const t = execTabs.get(execId);
            if (t) {
              t.label = data.label;
              renderTabBar();
            }
          }
        })
        .catch(() => {});
    }, 2000);

    return tab;
  }

  function renderTabBar() {
    if (execTabs.size === 0) {
      execTabBar.style.display = "none";
      return;
    }
    execTabBar.style.display = "flex";
    execTabBar.innerHTML = "";
    for (const [id, tab] of execTabs) {
      const tabEl = document.createElement("div");
      tabEl.className = "exec-tab" + (id === activeTabId ? " active" : "");
      tabEl.addEventListener("click", () => switchToTab(id));

      const dot = document.createElement("span");
      dot.className = "tab-status " + tab.status;
      tabEl.appendChild(dot);

      const label = document.createElement("span");
      label.className = "tab-label";
      label.textContent = tab.label;
      label.title = tab.label;
      tabEl.appendChild(label);

      const close = document.createElement("button");
      close.className = "tab-close";
      close.textContent = "\u00d7";
      close.addEventListener("click", (e) => {
        e.stopPropagation();
        closeTab(id);
      });
      tabEl.appendChild(close);

      execTabBar.appendChild(tabEl);
    }
  }

  function switchToTab(execId) {
    const tab = execTabs.get(execId);
    if (!tab) return;

    // Save state from current active tab
    if (activeTabId && activeTabId !== execId) {
      const oldTab = execTabs.get(activeTabId);
      if (oldTab) {
        oldTab.execLogUserAtBottom = execLogUserAtBottom;
        oldTab.childXAutoScroll = childXAutoScroll;
        oldTab.lastRenderedHistoryLen = lastRenderedHistoryLen;
        oldTab.logDiv.style.display = "none";
      }
    }

    // Activate new tab
    activeTabId = execId;
    executionId = execId;

    // Hide the original execLog (it's only used when there are no tabs)
    execLog.style.display = "none";
    tab.logDiv.style.display = "";

    // Restore tab's state to globals
    execLogUserAtBottom = tab.execLogUserAtBottom;
    childXAutoScroll = tab.childXAutoScroll;
    lastRenderedHistoryLen = tab.lastRenderedHistoryLen;

    // Update status display
    execStatus.textContent = tab.status;

    // Update run buttons based on tab status
    if (["completed", "error", "stopped"].includes(tab.status)) {
      showRunButtons("finished");
    } else if (tab.status === "interactive") {
      showRunButtons("interactive");
    } else {
      showRunButtons("running");
    }

    // Start polling for active tab if it's still running
    stopPolling();
    if (tab.status === "running" || tab.status === "paused_for_feedback" || tab.status === "interactive") {
      startPolling();
    }

    // Do an immediate poll to refresh the log
    if (tab.status === "running" || tab.status === "paused_for_feedback" || tab.status === "interactive") {
      pollExecution();
    }

    renderTabBar();
  }

  function closeTab(execId) {
    const tab = execTabs.get(execId);
    if (!tab) return;

    // Warn if still running
    if (tab.status === "running" || tab.status === "paused_for_feedback" || tab.status === "interactive") {
      if (!confirm("This execution is still active. Close the tab anyway?")) return;
    }

    // Stop background polling for this tab
    if (tab.bgPollInterval) {
      clearInterval(tab.bgPollInterval);
      tab.bgPollInterval = null;
    }

    // Remove from DOM
    tab.logDiv.remove();
    execTabs.delete(execId);

    // If closing the active tab, switch to another or reset
    if (execId === activeTabId) {
      stopPolling();
      socket.emit('unsubscribe_execution', { execution_id: execId });
      if (execTabs.size > 0) {
        const nextId = execTabs.keys().next().value;
        switchToTab(nextId);
      } else {
        activeTabId = null;
        executionId = null;
        execLog.style.display = "";
        execLog.innerHTML = "";
        lastRenderedHistoryLen = 0;
        execStatus.textContent = "";
        feedbackBar.classList.remove("active");
        interactiveBar.classList.remove("active");
        showRunButtons("idle");
      }
    }

    renderTabBar();
  }

  function clearAllTabs() {
    for (const [id, tab] of execTabs) {
      if (tab.bgPollInterval) clearInterval(tab.bgPollInterval);
      tab.logDiv.remove();
    }
    execTabs.clear();
    activeTabId = null;
    execTabBar.style.display = "none";
    execLog.style.display = "";
    execLog.innerHTML = "";
    lastRenderedHistoryLen = 0;
  }

  // --- Execution ---
  function showRunButtons(mode) {
    // mode: "idle" | "running" | "finished" | "interactive"
    // Always show Run button so users can start new executions
    $("btnRun").style.display = "";
    const btnEndChat = $("btnEndChat");
    if (mode === "idle") {
      $("btnCancel").style.display = "none";
      $("btnRestart").style.display = "none";
      if (btnEndChat) btnEndChat.style.display = "none";
    } else if (mode === "running") {
      $("btnCancel").style.display = "";
      $("btnRestart").style.display = "none";
      if (btnEndChat) btnEndChat.style.display = "none";
    } else if (mode === "interactive") {
      $("btnCancel").style.display = "none";
      $("btnRestart").style.display = "none";
      if (btnEndChat) btnEndChat.style.display = "";
    } else {
      $("btnCancel").style.display = "none";
      $("btnRestart").style.display = "";
      if (btnEndChat) btnEndChat.style.display = "none";
    }
  }

  // Run button -> show input modal
  $("btnRun").addEventListener("click", () => {
    if (!graph || !graph.start_node) {
      alert("Set a start node first.");
      return;
    }
    runInputData.value = lastInputData;
    includeOriginalQueryCheckbox.checked = lastIncludeOriginalQuery;
    runModal.classList.add("active");
    runInputData.focus();
  });

  // Cancel run modal
  $("btnCancelRun").addEventListener("click", () => {
    lastInputData = runInputData.value;
    lastIncludeOriginalQuery = includeOriginalQueryCheckbox.checked;
    runModal.classList.remove("active");
  });

  runModal.addEventListener("click", (e) => {
    if (e.target === runModal) {
      const btn = $("btnCancelRun");
      btn.classList.remove("pulse-hint");
      void btn.offsetWidth;
      btn.classList.add("pulse-hint");
    }
  });

  $("btnCancelRun").addEventListener("animationend", () => {
    $("btnCancelRun").classList.remove("pulse-hint");
  });

  // Confirm run -> start execution with input
  $("btnConfirmRun").addEventListener("click", async () => {
    runModal.classList.remove("active");
    await startExecution(runInputData.value.trim(), includeOriginalQueryCheckbox.checked);
  });

  async function startExecution(inputData, includeOriginalQuery = false) {
    lastInputData = inputData;
    lastIncludeOriginalQuery = !!includeOriginalQuery;

    // Ensure saved before running
    await api("PUT", `/api/graphs/${graph.id}`, graph);

    let res;
    if (attachedImages.length > 0) {
      const formData = new FormData();
      formData.append("input_data", inputData || "");
      if (includeOriginalQuery) formData.append("include_original_query", "true");
      attachedImages.forEach(f => formData.append("images", f));
      const resp = await fetch(`/api/execute/${graph.id}`, { method: "POST", body: formData });
      if (!resp.ok) throw new Error(`Execute failed: ${resp.status}`);
      res = await resp.json();
    } else {
      const body = {};
      if (inputData) body.input_data = inputData;
      if (includeOriginalQuery) body.include_original_query = true;
      res = await api("POST", `/api/execute/${graph.id}`, body);
    }

    clearAttachedImages();

    if (res.error) {
      alert(res.error);
      return;
    }

    const tab = createTab(res.execution_id, graph.id, inputData);
    executionId = res.execution_id;
    saveExecutionId(graph.id, executionId);
    feedbackBar.classList.remove("active");
    interactiveBar.classList.remove("active");
    bottomPanel.classList.remove("collapsed");
    showRunButtons("running");

    canvas.querySelectorAll(".node").forEach(el => {
      el.classList.remove("executing", "completed-node", "error-node");
    });

    startPolling();
    socket.emit('subscribe_execution', { execution_id: executionId });
  }

  // End Chat button -> end interactive session
  $("btnEndChat").addEventListener("click", async () => {
    if (!executionId) return;
    try {
      await api("POST", `/api/execution/${executionId}/end-interactive`);
    } catch (e) {
      console.error("Failed to end interactive session:", e);
    }
    stopPolling();
    interactiveBar.classList.remove("active");
    showRunButtons("finished");
    execStatus.textContent = "completed";
    const tab = execTabs.get(executionId);
    if (tab) {
      tab.status = "completed";
      renderTabBar();
    }
    if (graph) clearSavedExecutionId(graph.id);
  });

  // Cancel button -> stop execution
  $("btnCancel").addEventListener("click", async () => {
    if (!executionId) return;
    await api("POST", `/api/execution/${executionId}/stop`);
    stopPolling();
    interactiveBar.classList.remove("active");
    showRunButtons("finished");
    execStatus.textContent = "Cancelled";
    // Update tab status
    const tab = execTabs.get(executionId);
    if (tab) {
      tab.status = "stopped";
      renderTabBar();
    }
    if (graph) clearSavedExecutionId(graph.id);
  });

  // Restart button -> re-run from the beginning
  $("btnRestart").addEventListener("click", () => {
    if (!graph || !graph.start_node) return;
    canvas.querySelectorAll(".node").forEach(el => {
      el.classList.remove("executing", "completed-node", "error-node");
    });
    runInputData.value = lastInputData;
    includeOriginalQueryCheckbox.checked = lastIncludeOriginalQuery;
    runModal.classList.add("active");
    runInputData.focus();
  });

  // --- Reboot ---
  async function rebootServer() {
    const oldServerId = knownServerId;
    document.getElementById("rebootOverlay").classList.add("active");

    try {
      await fetch("/api/reboot", { method: "POST" });
    } catch (e) {
      // Expected - server dies mid-response sometimes
    }

    const poll = setInterval(async () => {
      try {
        const resp = await fetch("/api/server-id");
        if (resp.ok) {
          const data = await resp.json();
          if (data.server_id !== oldServerId) {
            clearInterval(poll);
            location.reload();
          }
        }
      } catch (e) {
        // Server still down, keep polling
      }
    }, 1000);
  }

  $("btnRestartServer").addEventListener("click", rebootServer);

  $("btnSubmitFeedback").addEventListener("click", submitFeedback);
  feedbackInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitFeedback();
  });

  async function submitFeedback() {
    if (!executionId) return;
    const response = feedbackInput.value.trim();
    if (!response) return;
    await api("POST", `/api/execution/${executionId}/feedback`, { response });
    feedbackInput.value = "";
    feedbackBar.classList.remove("active");
  }

  // --- Interactive messaging ---
  function sendInteractiveMessage() {
    if (!executionId) return;
    const text = interactiveInput.value.trim();
    if (!text) return;
    interactiveInput.value = "";
    socket.emit('user_message', { execution_id: executionId, message: text });
  }

  $("btnSendInteractive").addEventListener("click", sendInteractiveMessage);
  interactiveInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendInteractiveMessage();
  });

  socket.on('message_ack', (data) => {
    if (data.status === 'failed') {
      interactiveInput.placeholder = "Send failed — process may have ended";
      setTimeout(() => { interactiveInput.placeholder = "Type a message to Claude..."; }, 3000);
    }
  });

  bottomPanelHeader.addEventListener("click", () => {
    bottomPanel.classList.toggle("collapsed");
    bottomPanel.classList.remove("fullscreen");
    bottomPanel.style.height = "";
    bottomPanel.style.maxHeight = "";
    btnFullscreen.textContent = "\u2922";
  });

  // --- Drag handle: resize bottom panel ---
  dragHandle.addEventListener("mousedown", (e) => {
    isPanelDragging = true;
    panelDragStartY = e.clientY;
    panelDragStartHeight = bottomPanel.offsetHeight;
    bottomPanel.classList.add("dragging");
    bottomPanel.classList.remove("collapsed");
    e.preventDefault();
  });

  document.addEventListener("mousemove", (e) => {
    if (!isPanelDragging) return;
    const newHeight = Math.max(36, Math.min(window.innerHeight - 50, panelDragStartHeight + (panelDragStartY - e.clientY)));
    bottomPanel.style.height = newHeight + "px";
    bottomPanel.style.maxHeight = newHeight + "px";
  });

  document.addEventListener("mouseup", () => {
    if (!isPanelDragging) return;
    isPanelDragging = false;
    bottomPanel.classList.remove("dragging");
    if (bottomPanel.offsetHeight <= 36) {
      bottomPanel.classList.add("collapsed");
      bottomPanel.style.height = "";
      bottomPanel.style.maxHeight = "";
    }
  });

  // --- Fullscreen toggle ---
  btnFullscreen.addEventListener("click", (e) => {
    e.stopPropagation();
    bottomPanel.classList.toggle("fullscreen");
    bottomPanel.classList.remove("collapsed");
    bottomPanel.style.height = "";
    bottomPanel.style.maxHeight = "";
    btnFullscreen.textContent = bottomPanel.classList.contains("fullscreen") ? "\u2921" : "\u2922";
  });

  bottomPanel.querySelector(".bottom-panel-content").addEventListener("scroll", (e) => {
    if (isAutoScrolling) return;
    const el = e.target;
    execLogUserAtBottom = (el.scrollHeight - el.scrollTop - el.clientHeight) < 50;
    // Also update active tab's state
    if (activeTabId) {
      const tab = execTabs.get(activeTabId);
      if (tab) tab.execLogUserAtBottom = execLogUserAtBottom;
    }
  });

  function startPolling() {
    stopPolling();
    pollInterval = setInterval(pollExecution, 2000);
    pollExecution(); // immediate first poll
  }

  function stopPolling() {
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
  }

  async function pollExecution() {
    if (!executionId) return;
    const state = await api("GET", `/api/execution/${executionId}`);
    if (state.error) return;

    // Update tab status
    const tab = execTabs.get(executionId);
    if (tab) {
      tab.status = state.status;
      renderTabBar();
    }

    execStatus.textContent = state.status;

    // Update node classes - only for active tab
    if (executionId === activeTabId) {
      const ag = getActiveGraph();
      canvas.querySelectorAll(".node").forEach(el => {
        el.classList.remove("executing", "completed-node", "error-node");
      });

      const visitedIds = new Set(state.history.map(h => h.node_id));
      visitedIds.forEach(nid => {
        if (ag.nodes[nid]) {
          const el = canvas.querySelector(`[data-node-id="${nid}"]`);
          if (el) el.classList.add("completed-node");
        }
      });

      if (state.current_node && (state.status === "running" || state.status === "paused_for_feedback" || state.status === "interactive")) {
        if (ag.nodes[state.current_node]) {
          const el = canvas.querySelector(`[data-node-id="${state.current_node}"]`);
          if (el) {
            el.classList.remove("completed-node");
            el.classList.add("executing");
          }
        }
      }

      if (state.status === "error" && state.history.length > 0) {
        const lastEntry = state.history[state.history.length - 1];
        if (ag.nodes[lastEntry.node_id]) {
          const el = canvas.querySelector(`[data-node-id="${lastEntry.node_id}"]`);
          if (el) {
            el.classList.remove("completed-node", "executing");
            el.classList.add("error-node");
          }
        }
      }
    }

    renderExecLog(state.history, state.live_output, state.status, tab);

    if (state.status === "paused_for_feedback" && state.pending_question) {
      feedbackQuestion.textContent = state.pending_question;
      feedbackBar.classList.add("active");
    } else {
      feedbackBar.classList.remove("active");
    }

    // Show interactive bar when execution is running or interactive
    if (state.status === "running" || state.status === "interactive") {
      interactiveBar.classList.add("active");
    } else {
      interactiveBar.classList.remove("active");
    }

    if (state.status === "interactive") {
      showRunButtons("interactive");
    } else if (["completed", "error", "stopped"].includes(state.status)) {
      stopPolling();
      socket.emit('unsubscribe_execution', { execution_id: executionId });
      showRunButtons("finished");
      interactiveBar.classList.remove("active");
      if (graph) clearSavedExecutionId(graph.id);
      // Delay to let post-execution commit finish
      setTimeout(fetchTimeline, 3000);
    }
  }

  function renderExecLog(history, liveOutput, execState, tab) {
    // Use tab-specific state if provided, otherwise fall back to globals
    const targetLog = tab ? tab.logDiv : execLog;
    const tabHistLen = tab ? tab.lastRenderedHistoryLen : lastRenderedHistoryLen;
    const tabUserAtBottom = tab ? tab.execLogUserAtBottom : execLogUserAtBottom;
    const tabChildXScroll = tab ? tab.childXAutoScroll : childXAutoScroll;
    const shouldScroll = tabUserAtBottom;
    isAutoScrolling = true;

    if (history.length < tabHistLen) {
      targetLog.innerHTML = "";
      if (tab) tab.lastRenderedHistoryLen = 0;
      else lastRenderedHistoryLen = 0;
    }

    const currentHistLen = tab ? tab.lastRenderedHistoryLen : lastRenderedHistoryLen;
    let liveDiv = targetLog.querySelector(".live-output-entry");

    for (let i = currentHistLen; i < history.length; i++) {
      const entry = history[i];
      const div = document.createElement("div");
      div.className = "log-entry";

      let html = `<span class="log-node">[${esc(entry.node_name || entry.node_id)}]</span> `;
      if (entry.error) {
        html += `<span class="log-error">ERROR: ${esc(entry.error)}</span>`;
      } else if (entry.transition_taken) {
        html += `<span class="log-transition">-> ${esc(entry.transition_taken)}</span>`;
      } else {
        html += `<span style="color:#27ae60">(terminal)</span>`;
      }
      if (entry.timestamp) {
        const t = new Date(entry.timestamp);
        html += `<span class="log-time">${t.toLocaleTimeString()}</span>`;
      }
      if (entry.prompt_sent) {
        html += `
          <details>
            <summary>Show prompt sent</summary>
            <pre>${esc(entry.prompt_sent)}</pre>
          </details>`;
      }
      if (entry.raw_response) {
        html += `
          <details>
            <summary>Show response</summary>
            <pre>${esc(entry.raw_response)}</pre>
          </details>`;
      }
      if (entry.data_passed) {
        html += `
          <details>
            <summary>Show data passed</summary>
            <pre>${esc(entry.data_passed)}</pre>
          </details>`;
      }
      div.innerHTML = html;
      if (liveDiv) {
        targetLog.insertBefore(div, liveDiv);
      } else {
        targetLog.appendChild(div);
      }
    }
    if (tab) tab.lastRenderedHistoryLen = history.length;
    else lastRenderedHistoryLen = history.length;

    // Also sync to global if this is the active tab
    if (tab && tab.id === activeTabId) {
      lastRenderedHistoryLen = tab.lastRenderedHistoryLen;
    }

    if (execState === "running" || execState === "interactive") {
      const statusLabel = execState === "interactive" ? "interactive chat" : "running...";
      const statusColor = execState === "interactive" ? "#3b82f6" : "#e2b714";
      let liveHtml;
      if (liveOutput) {
        liveHtml = `
          <span class="log-node" style="color:${statusColor}">[${statusLabel}]</span>
          <details open>
            <summary>Live output</summary>
            <pre>${esc(liveOutput)}</pre>
          </details>`;
      } else {
        liveHtml = `
          <span class="log-node" style="color:${statusColor}">[${statusLabel}]</span>
          <span style="color:#888; font-style:italic"> Waiting for Claude CLI output...</span>`;
      }
      if (liveDiv) {
        const existingPre = liveDiv.querySelector("pre");
        if (existingPre && liveOutput) {
          isAutoScrolling = true;
          existingPre.textContent = liveOutput;
          if (tabChildXScroll) existingPre.scrollTop = existingPre.scrollHeight;
          isAutoScrolling = false;
        } else {
          liveDiv.innerHTML = liveHtml;
          const newPre = liveDiv.querySelector("pre");
          if (newPre) newPre.scrollTop = newPre.scrollHeight;
        }
      } else {
        liveDiv = document.createElement("div");
        liveDiv.className = "log-entry live-output-entry";
        liveDiv.innerHTML = liveHtml;
        targetLog.appendChild(liveDiv);
        const createdPre = liveDiv.querySelector("pre");
        if (createdPre) createdPre.scrollTop = createdPre.scrollHeight;
      }
    } else {
      if (liveDiv) liveDiv.remove();
    }

    if (shouldScroll) {
      requestAnimationFrame(() => {
        const content = bottomPanel.querySelector(".bottom-panel-content");
        content.scrollTop = content.scrollHeight;
        requestAnimationFrame(() => {
          isAutoScrolling = false;
        });
      });
    } else {
      requestAnimationFrame(() => {
        isAutoScrolling = false;
      });
    }
  }

  // --- Utility ---
  function esc(str) {
    if (!str) return "";
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  // --- Execution reconnection ---
  async function tryReconnectExecution() {
    if (!graph) return false;

    // Check localStorage first
    let candidateExecId = getSavedExecutionId(graph.id);

    if (candidateExecId) {
      try {
        const state = await api("GET", `/api/execution/${candidateExecId}`);
        if (!state.error) {
          reconnectToExecution(candidateExecId, state);
          return true;
        }
      } catch (e) { /* execution not found */ }
      clearSavedExecutionId(graph.id);
    }

    // Fallback: ask backend for any active execution on this graph
    try {
      const resp = await api("GET", `/api/graph/${graph.id}/active-execution`);
      if (resp.active) {
        const state = await api("GET", `/api/execution/${resp.execution_id}`);
        reconnectToExecution(resp.execution_id, state);
        return true;
      }
    } catch (e) { /* no active execution */ }

    return false;
  }

  function reconnectToExecution(execId, state) {
    // Create a tab for the reconnected execution
    const inputData = state.incoming_data || "";
    const tab = createTab(execId, graph.id, inputData);
    tab.status = state.status;

    executionId = execId;
    saveExecutionId(graph.id, execId);
    bottomPanel.classList.remove("collapsed");

    // Render current state into the tab
    renderExecLog(state.history, state.live_output, state.status, tab);
    execStatus.textContent = state.status;

    // Handle feedback bar
    if (state.status === "paused_for_feedback" && state.pending_question) {
      feedbackQuestion.textContent = state.pending_question;
      feedbackBar.classList.add("active");
    } else {
      feedbackBar.classList.remove("active");
    }

    // Handle interactive bar
    if (state.status === "running" || state.status === "interactive") {
      interactiveBar.classList.add("active");
    } else {
      interactiveBar.classList.remove("active");
    }

    // Set buttons and polling based on status
    if (["completed", "error", "stopped"].includes(state.status)) {
      showRunButtons("finished");
      clearSavedExecutionId(graph.id);
    } else if (state.status === "interactive") {
      showRunButtons("interactive");
      startPolling();
      socket.emit('subscribe_execution', { execution_id: executionId });
    } else {
      showRunButtons("running");
      startPolling();
      socket.emit('subscribe_execution', { execution_id: executionId });
    }

    renderTabBar();
  }

  // --- Init ---
  async function init() {
    // Load server-persisted working directory history
    await fetchWorkingDirHistory();

    // One-time migration from localStorage to server
    const oldDirHistory = localStorage.getItem("workingDirHistory");
    if (oldDirHistory && workingDirCache.length === 0) {
      try {
        const oldDirs = JSON.parse(oldDirHistory);
        if (Array.isArray(oldDirs) && oldDirs.length > 0) {
          for (const dir of oldDirs.reverse()) {
            await api("POST", "/api/working-dirs", { dir });
          }
          await fetchWorkingDirHistory();
          localStorage.removeItem("workingDirHistory");
        }
      } catch (e) {
        console.error("Working dir migration failed", e);
      }
    } else if (oldDirHistory) {
      localStorage.removeItem("workingDirHistory");
    }

    const graphs = await api("GET", "/api/graphs");
    if (graphs.length > 0) {
      const lastId = localStorage.getItem("lastGraphId");
      const match = lastId && graphs.find(g => g.id === lastId);
      await loadGraph(match ? match.id : graphs[0].id);
      // loadGraph already calls tryReconnectExecution internally
    } else {
      await createNewGraph();
      showRunButtons("idle");
    }

    // Fetch initial server ID for reboot detection
    try {
      const sidResp = await api("GET", "/api/server-id");
      knownServerId = sidResp.server_id;
    } catch (e) {
      // Non-critical
    }

    // Poll for code changes to toggle reboot button glow
    setInterval(async () => {
      try {
        const resp = await fetch("/api/code-changed");
        if (resp.ok) {
          const data = await resp.json();
          const btn = document.getElementById("btnRestartServer");
          if (data.changed) {
            btn.classList.add("code-changed");
          } else {
            btn.classList.remove("code-changed");
          }
        }
      } catch (e) {
        // Server unreachable, ignore
      }
    }, 5000);
  }

  init();
})();
