"use strict";

// OBS Browser Source page. Gets chat from the overlay app over a WebSocket.
(() => {
  const params = new URLSearchParams(location.search);
  const pinnedTab = ["twitch", "tiktok", "merge"].includes(params.get("tab")) ? params.get("tab") : null;
  const sizeParam = Number(params.get("size")) || 0;
  const boxesParam = params.has("boxes") ? params.get("boxes") !== "0" : null;
  const fadeOn = params.get("fade") !== "0";
  // Connection news, renames and warnings are for you, not your viewers.
  const showSystem = params.get("system") === "1";
  const maxLines = Math.max(5, Math.min(200, Number(params.get("lines")) || 60));
  const HIDDEN_KINDS = new Set(["system", "error", "warn"]);

  const chat = document.getElementById("chat");
  const state = { tab: "twitch", history: {}, visible: true };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function wanted(msg) {
    return showSystem || !HIDDEN_KINDS.has(msg.k);
  }

  function renderLine(msg, tab) {
    const line = el("div", `line k-${msg.k} p-${msg.p}`);
    line.dataset.id = msg.id;
    if (msg.mention) line.classList.add("mention");
    const box = el("span", "box");
    if (tab === "merge") box.appendChild(el("span", "bar"));

    if (msg.u) {
      const name = el("span", "name", msg.u.show || msg.u.name);
      name.dataset.key = msg.u.key;
      name.dataset.name = msg.u.name;
      name.style.color = msg.u.color;
      const body = el("span", "body");
      if (msg.k === "action") {
        box.append(el("span", "body", "* "), name, el("span", "body", " "));
        body.style.color = msg.u.color;
      } else {
        box.append(name, el("span", "colon", ": "));
      }
      for (const seg of msg.seg || []) {
        if (seg.t === "emote") {
          const img = el("img", "emote");
          img.src = seg.hd || seg.url;
          img.alt = seg.name;
          img.title = seg.name;
          img.onerror = () => img.replaceWith(document.createTextNode(seg.name));
          body.appendChild(img);
        } else if (seg.t === "mention") {
          body.appendChild(el("span", "mention-word", seg.v));
        } else {
          body.appendChild(document.createTextNode(seg.v));
        }
      }
      box.appendChild(body);
    } else {
      box.appendChild(document.createTextNode(msg.text || ""));
    }
    line.appendChild(box);
    return line;
  }

  function currentTab() {
    return pinnedTab || state.tab;
  }

  function trim() {
    while (chat.children.length > maxLines) chat.firstElementChild.remove();
    // Lines that scrolled off the top are useless in OBS: drop them too.
    while (chat.firstElementChild && chat.scrollHeight > chat.clientHeight + 4 &&
           chat.firstElementChild.getBoundingClientRect().bottom < 0) {
      chat.firstElementChild.remove();
    }
  }

  function renderAll() {
    const tab = currentTab();
    chat.replaceChildren();
    const lines = (state.history[tab] || []).filter(wanted).slice(-maxLines);
    for (const msg of lines) {
      const node = renderLine(msg, tab);
      node.style.animation = "none";
      chat.appendChild(node);
    }
    trim();
  }

  function applySettings(settings) {
    if (!settings) return;
    const px = sizeParam || Math.round((settings.font_size || 11) * 1.36);
    document.documentElement.style.setProperty("--size", `${px}px`);
    const boxes = boxesParam !== null ? boxesParam : settings.text_backing !== false;
    document.body.classList.toggle("boxes", boxes);
  }

  function applyVisible(visible) {
    state.visible = visible;
    chat.classList.toggle("hidden", fadeOn && !visible);
  }

  function remember(tab, msg) {
    const list = state.history[tab] || (state.history[tab] = []);
    list.push(msg);
    if (list.length > 200) list.splice(0, list.length - 200);
  }

  function handle(data) {
    switch (data.type) {
      case "hello":
        state.tab = data.tab;
        state.history = data.history || {};
        applySettings(data.settings);
        applyVisible(data.visible !== false);
        renderAll();
        break;
      case "message":
        for (const tab of data.tabs) remember(tab, data.msg);
        if (data.tabs.includes(currentTab()) && wanted(data.msg)) {
          chat.appendChild(renderLine(data.msg, currentTab()));
          trim();
        }
        break;
      case "remove": {
        const ids = new Set(data.ids);
        for (const tab of Object.keys(state.history)) {
          state.history[tab] = state.history[tab].filter((m) => !ids.has(m.id));
        }
        for (const node of [...chat.children]) {
          if (ids.has(Number(node.dataset.id))) node.remove();
        }
        break;
      }
      case "tab":
        state.tab = data.tab;
        if (!pinnedTab) renderAll();
        break;
      case "settings":
        applySettings(data);
        break;
      case "visible":
        applyVisible(data.visible);
        break;
      case "alias":
        for (const list of Object.values(state.history)) {
          for (const m of list) if (m.u && m.u.key === data.key) m.u.show = data.show || m.u.name;
        }
        for (const node of chat.querySelectorAll(".name")) {
          if (node.dataset.key === data.key) node.textContent = data.show || node.dataset.name;
        }
        break;
      default:
        break;
    }
  }

  let delay = 500;
  function connect() {
    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onopen = () => { delay = 500; };
    ws.onmessage = (event) => {
      try { handle(JSON.parse(event.data)); } catch (err) { console.error(err); }
    };
    ws.onclose = () => {
      setTimeout(connect, delay);
      delay = Math.min(delay * 2, 5000);
    };
  }

  window.addEventListener("resize", trim);
  connect();
})();
