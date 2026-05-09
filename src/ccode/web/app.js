const terminalEl = document.getElementById("terminal");
const sessionListEl = document.getElementById("session-list");
const tokenFormEl = document.getElementById("token-form");
const tokenInputEl = document.getElementById("token-input");
const statusEl = document.getElementById("status");
const backButton = document.getElementById("back");
const refreshButton = document.getElementById("refresh");
const changeTokenButton = document.getElementById("change-token");
const reconnectButton = document.getElementById("reconnect");
const focusButton = document.getElementById("focus");

const tokenStorageKey = "ccode.remote.token";
let token = window.sessionStorage.getItem(tokenStorageKey) || "";
let socket = null;
let term = null;
let fitAddon = null;
let currentSessionId = null;

function setStatus(message) {
  statusEl.textContent = message;
}

function ensureTerminal() {
  if (term) {
    return;
  }
  term = new Terminal({
    cursorBlink: true,
    convertEol: true,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
    fontSize: 14,
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
  term.onData((data) => {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }
    socket.send(JSON.stringify({ type: "input", data }));
  });
}

function fitAndSendResize() {
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
  currentSessionId = null;
  terminalEl.hidden = true;
  sessionListEl.hidden = true;
  tokenFormEl.hidden = false;
  backButton.hidden = true;
  focusButton.hidden = true;
  reconnectButton.hidden = true;
  refreshButton.hidden = true;
  changeTokenButton.hidden = true;
  setStatus(message);
  tokenInputEl.value = token;
  tokenInputEl.focus();
}

function showListMode() {
  closeSocket();
  currentSessionId = null;
  tokenFormEl.hidden = true;
  terminalEl.hidden = true;
  sessionListEl.hidden = false;
  backButton.hidden = true;
  focusButton.hidden = true;
  reconnectButton.hidden = true;
  refreshButton.hidden = false;
  changeTokenButton.hidden = false;
}

function showTerminalMode() {
  tokenFormEl.hidden = true;
  sessionListEl.hidden = true;
  terminalEl.hidden = false;
  backButton.hidden = false;
  focusButton.hidden = false;
  reconnectButton.hidden = false;
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

  setStatus(`${sessions.length} running session${sessions.length === 1 ? "" : "s"}`);
  for (const session of sessions) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "session-card";

    const title = document.createElement("strong");
    title.textContent = session.title || session.id || "ccode session";

    const cwd = document.createElement("span");
    cwd.textContent = session.cwd || "unknown cwd";

    const tmux = document.createElement("code");
    tmux.textContent = session.tmux_session || session.id || "unknown tmux session";

    const created = document.createElement("small");
    created.textContent = `created ${formatDate(session.created_at)}`;

    card.append(title, cwd, tmux, created);
    card.addEventListener("click", () => openSession(session.id));
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

function connect() {
  if (!token) {
    showTokenMode();
    return;
  }
  if (!currentSessionId) {
    setStatus("choose a session first");
    return;
  }

  ensureTerminal();
  closeSocket();
  term.clear();
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${protocol}//${window.location.host}/ws/${encodeURIComponent(currentSessionId)}`);
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

function openSession(sessionId) {
  currentSessionId = sessionId;
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
window.addEventListener("resize", fitAndSendResize);
backButton.addEventListener("click", loadSessions);
refreshButton.addEventListener("click", loadSessions);
reconnectButton.addEventListener("click", connect);
focusButton.addEventListener("click", () => {
  ensureTerminal();
  term.focus();
});

loadSessions();
