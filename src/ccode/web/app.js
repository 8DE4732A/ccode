const terminalEl = document.getElementById("terminal");
const sessionListEl = document.getElementById("session-list");
const tokenFormEl = document.getElementById("token-form");
const tokenInputEl = document.getElementById("token-input");
const statusEl = document.getElementById("status");
const actionsEl = document.getElementById("actions");
const actionsToggleButton = document.getElementById("actions-toggle");
const backButton = document.getElementById("back");
const refreshButton = document.getElementById("refresh");
const changeTokenButton = document.getElementById("change-token");
const reconnectButton = document.getElementById("reconnect");
const focusButton = document.getElementById("focus");
const mobileKeybarEl = document.getElementById("mobile-keybar");

const inputSequences = {
  tab: "\t",
  esc: "\x1b",
  "ctrl-c": "\x03",
  "ctrl-d": "\x04",
  "ctrl-l": "\x0c",
  enter: "\r",
  backspace: "\x7f",
  up: "\x1b[A",
  down: "\x1b[B",
  right: "\x1b[C",
  left: "\x1b[D",
};

const tokenStorageKey = "ccode.remote.adminToken";
const fontSizeStorageKey = "ccode.remote.terminalFontSize";
let token = window.sessionStorage.getItem(tokenStorageKey) || "";
let socket = null;
let term = null;
let fitAddon = null;
let currentSession = null;
let terminalFontSize = Number(window.localStorage.getItem(fontSizeStorageKey)) || 14;
let terminalTouchScroll = null;
let terminalViewportEl = null;
let resizeFrame = null;
let viewportHeight = "";
let keyboardInset = "";

function setStatus(message) {
  statusEl.textContent = message;
}

function sendInput(data) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  socket.send(JSON.stringify({ type: "input", data }));
}

function focusTerminal() {
  ensureTerminal();
  term.focus();
}

function adjustTerminalFontSize(delta) {
  const nextFontSize = Math.min(22, Math.max(10, terminalFontSize + delta));
  if (nextFontSize === terminalFontSize) {
    return;
  }
  terminalFontSize = nextFontSize;
  window.localStorage.setItem(fontSizeStorageKey, String(terminalFontSize));
  if (term) {
    term.options.fontSize = terminalFontSize;
    fitAndSendResize();
  }
}

function closeActionMenu() {
  actionsEl.classList.remove("open");
  actionsToggleButton.setAttribute("aria-expanded", "false");
}

function beginTerminalTouchScroll(y) {
  if (!term || terminalTouchScroll) {
    return;
  }
  terminalTouchScroll = { y, remainder: 0, active: false };
}

function scrollTerminalFromTouch(y, event) {
  if (!terminalTouchScroll || !term) {
    return;
  }
  const deltaY = terminalTouchScroll.y - y;
  if (!terminalTouchScroll.active && Math.abs(deltaY) < 8) {
    return;
  }
  terminalTouchScroll.active = true;
  event.preventDefault();
  if (terminalViewportEl) {
    terminalViewportEl.dispatchEvent(new WheelEvent("wheel", { deltaY, bubbles: true, cancelable: true }));
  }
  const lineHeight = Math.max(14, terminalFontSize * 1.35);
  const delta = deltaY + terminalTouchScroll.remainder;
  const lines = Math.trunc(delta / lineHeight);
  terminalTouchScroll.remainder = delta - lines * lineHeight;
  terminalTouchScroll.y = y;
  if (lines) {
    term.scrollLines(lines);
  }
}

function endTerminalTouchScroll() {
  terminalTouchScroll = null;
}

function startTerminalTouchScroll(event) {
  if (event.touches.length === 1) {
    beginTerminalTouchScroll(event.touches[0].clientY);
  }
}

function moveTerminalTouchScroll(event) {
  if (event.touches.length === 1) {
    scrollTerminalFromTouch(event.touches[0].clientY, event);
  }
}

function stopTerminalTouchScroll() {
  endTerminalTouchScroll();
}

function ensureTerminal() {
  if (term) {
    return;
  }
  term = new Terminal({
    cursorBlink: true,
    convertEol: true,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
    fontSize: terminalFontSize,
    theme: {
      background: "#05070a",
      foreground: "#d6e2f0",
      cursor: "#e8eef8",
      selectionBackground: "#2e4266",
    },
  });
  fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(terminalEl);
  terminalViewportEl = terminalEl.querySelector(".xterm-viewport");
  term.onData(sendInput);
}

function updateViewportInsets() {
  const viewport = window.visualViewport;
  const nextViewportHeight = `${viewport ? viewport.height : window.innerHeight}px`;
  const nextKeyboardInset = `${viewport ? Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop) : 0}px`;
  if (nextViewportHeight !== viewportHeight) {
    viewportHeight = nextViewportHeight;
    document.documentElement.style.setProperty("--viewport-height", viewportHeight);
  }
  if (nextKeyboardInset !== keyboardInset) {
    keyboardInset = nextKeyboardInset;
    document.documentElement.style.setProperty("--keyboard-inset", keyboardInset);
  }
}

function requestFitAndSendResize() {
  if (resizeFrame !== null) {
    return;
  }
  resizeFrame = window.requestAnimationFrame(() => {
    resizeFrame = null;
    fitAndSendResize();
  });
}

function fitAndSendResize() {
  updateViewportInsets();
  if (!term || !fitAddon || terminalEl.hidden) {
    return;
  }
  fitAddon.fit();
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  socket.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
}

function closeSocket() {
  if (socket) {
    socket.close();
    socket = null;
  }
}

function showTokenMode(message = "enter token") {
  closeSocket();
  currentSession = null;
  terminalEl.hidden = true;
  sessionListEl.hidden = true;
  tokenFormEl.hidden = false;
  actionsToggleButton.hidden = true;
  closeActionMenu();
  backButton.hidden = true;
  focusButton.hidden = true;
  reconnectButton.hidden = true;
  mobileKeybarEl.hidden = true;
  refreshButton.hidden = true;
  changeTokenButton.hidden = true;
  setStatus(message);
  tokenInputEl.value = token;
  tokenInputEl.focus();
}

function showListMode() {
  closeSocket();
  currentSession = null;
  tokenFormEl.hidden = true;
  terminalEl.hidden = true;
  sessionListEl.hidden = false;
  actionsToggleButton.hidden = false;
  closeActionMenu();
  backButton.hidden = true;
  focusButton.hidden = true;
  reconnectButton.hidden = true;
  mobileKeybarEl.hidden = true;
  refreshButton.hidden = false;
  changeTokenButton.hidden = false;
}

function showTerminalMode() {
  tokenFormEl.hidden = true;
  sessionListEl.hidden = true;
  terminalEl.hidden = false;
  actionsToggleButton.hidden = false;
  closeActionMenu();
  backButton.hidden = false;
  focusButton.hidden = false;
  reconnectButton.hidden = false;
  mobileKeybarEl.hidden = false;
  refreshButton.hidden = true;
  changeTokenButton.hidden = false;
}

function formatDate(value) {
  if (!value) {
    return "unknown";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function renderSessions(sessions) {
  sessionListEl.replaceChildren();
  if (!sessions.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No running remote sessions. Start ccode with remote enabled, then refresh.";
    sessionListEl.append(empty);
    setStatus("no running sessions");
    return;
  }

  const onlineCount = sessions.filter((session) => session.online !== false).length;
  setStatus(`${onlineCount}/${sessions.length} online session${sessions.length === 1 ? "" : "s"}`);
  for (const session of sessions) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "session-card";
    const online = session.online !== false;
    if (!online) {
      card.disabled = true;
      card.classList.add("offline");
    }

    const title = document.createElement("strong");
    title.textContent = session.title || session.id || "ccode session";

    const device = document.createElement("span");
    device.textContent = session.device_id
      ? `device ${session.device_name || session.device_id} · ${online ? "online" : "offline"}`
      : "local hub";

    const cwd = document.createElement("span");
    cwd.textContent = session.cwd || "unknown cwd";

    const tmux = document.createElement("code");
    tmux.textContent = session.tmux_session || session.id || "unknown tmux session";

    const created = document.createElement("small");
    created.textContent = `created ${formatDate(session.created_at)}`;

    card.append(title, device, cwd, tmux, created);
    card.addEventListener("click", () => openSession(session));
    sessionListEl.append(card);
  }
}

async function loadSessions() {
  if (!token) {
    showTokenMode();
    return;
  }

  showListMode();
  setStatus("loading sessions");
  try {
    const response = await fetch("/api/sessions", {
      cache: "no-store",
      headers: { Authorization: `Bearer ${token}` },
    });
    if (response.status === 401) {
      window.sessionStorage.removeItem(tokenStorageKey);
      token = "";
      showTokenMode("unauthorized: invalid token");
      return;
    }
    if (!response.ok) {
      setStatus(`failed to load sessions: ${response.status}`);
      return;
    }
    const payload = await response.json();
    renderSessions(Array.isArray(payload.sessions) ? payload.sessions : []);
  } catch (error) {
    setStatus("failed to load sessions");
  }
}

function sessionWsPath(session) {
  if (session.device_id) {
    return `/ws/devices/${encodeURIComponent(session.device_id)}/sessions/${encodeURIComponent(session.id)}`;
  }
  return `/ws/${encodeURIComponent(session.id)}`;
}

function connect() {
  if (!token) {
    showTokenMode();
    return;
  }
  if (!currentSession) {
    setStatus("choose a session first");
    return;
  }

  ensureTerminal();
  closeSocket();
  term.clear();
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${protocol}//${window.location.host}${sessionWsPath(currentSession)}`);
  setStatus("connecting");

  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({ type: "auth", token }));
  });

  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "output") {
      term.write(payload.data || "");
    } else if (payload.type === "status") {
      setStatus(payload.message || "connected");
      if ((payload.message || "") === "connected") {
        fitAndSendResize();
        term.focus();
      }
    }
  });

  socket.addEventListener("close", (event) => {
    if (event.code === 1008) {
      if (statusEl.textContent === "unauthorized: invalid token") {
        window.sessionStorage.removeItem(tokenStorageKey);
        token = "";
        showTokenMode("unauthorized: invalid token");
      }
      return;
    }
    if (event.code !== 1000 && statusEl.textContent === "connected") {
      setStatus("disconnected");
    }
  });

  socket.addEventListener("error", () => {
    setStatus("connection error");
  });
}

function openSession(session) {
  currentSession = session;
  showTerminalMode();
  ensureTerminal();
  fitAndSendResize();
  connect();
}

tokenFormEl.addEventListener("submit", (event) => {
  event.preventDefault();
  token = tokenInputEl.value.trim();
  if (!token) {
    showTokenMode("enter token");
    return;
  }
  window.sessionStorage.setItem(tokenStorageKey, token);
  loadSessions();
});

changeTokenButton.addEventListener("click", () => {
  window.sessionStorage.removeItem(tokenStorageKey);
  token = "";
  showTokenMode("enter token");
});
window.addEventListener("resize", requestFitAndSendResize);
actionsToggleButton.addEventListener("click", () => {
  const open = actionsEl.classList.toggle("open");
  actionsToggleButton.setAttribute("aria-expanded", String(open));
});
actionsEl.addEventListener("click", (event) => {
  if (event.target.closest("button")) {
    closeActionMenu();
  }
});
backButton.addEventListener("click", loadSessions);
refreshButton.addEventListener("click", loadSessions);
reconnectButton.addEventListener("click", connect);
focusButton.addEventListener("click", focusTerminal);
terminalEl.addEventListener("touchstart", startTerminalTouchScroll, { passive: true });
terminalEl.addEventListener("touchmove", moveTerminalTouchScroll, { passive: false });
terminalEl.addEventListener("touchend", stopTerminalTouchScroll);
terminalEl.addEventListener("touchcancel", stopTerminalTouchScroll);
mobileKeybarEl.addEventListener("pointerdown", (event) => {
  const inputButton = event.target.closest("button[data-sequence]");
  const zoomButton = event.target.closest("button[data-zoom]");
  if (!inputButton && !zoomButton) {
    return;
  }
  event.preventDefault();
  if (zoomButton) {
    adjustTerminalFontSize(Number(zoomButton.dataset.zoom));
    return;
  }
  const data = inputSequences[inputButton.dataset.sequence];
  if (data) {
    sendInput(data);
    focusTerminal();
  }
});
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", requestFitAndSendResize);
  window.visualViewport.addEventListener("scroll", requestFitAndSendResize);
}
updateViewportInsets();

loadSessions();
