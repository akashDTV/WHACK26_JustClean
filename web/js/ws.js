// WebSocket client: one connection to Python, auto-reconnect with backoff.
// Open the page with ?fake=1 to skip the server and use fake.js instead.

(function () {
  const params = new URLSearchParams(location.search);
  // Opening index.html straight from disk (file://) has no server, so use fake mode automatically.
  const FAKE = params.has("fake") || location.protocol === "file:";
  const WS_URL = params.get("ws") || `${location.protocol === "https:" ? "wss" : "ws"}://${location.host || "localhost:8000"}/ws`;
  const conn = document.getElementById("conn");

  function setConn(state, text) {
    conn.dataset.state = state;
    conn.textContent = text;
  }

  const Transport = {
    ws: null,
    retry: 0,
    outbox: [],

    connect() {
      setConn("connecting", "Connecting to the coach…");
      let ws;
      try { ws = new WebSocket(WS_URL); } catch (e) { return this.scheduleReconnect(); }
      this.ws = ws;

      ws.onopen = () => {
        this.retry = 0;
        setConn("open", "Connected");
        // ask Python for the current state + clip list so a reload lands on the right screen
        this.send({ type: "command", name: "hello" });
        this.outbox.splice(0).forEach((m) => ws.send(JSON.stringify(m)));
      };
      ws.onmessage = (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch (err) { console.warn("[ws] bad JSON", e.data); return; }
        window.App.handle(msg);
      };
      ws.onclose = () => { setConn("closed", "Lost the coach — reconnecting…"); this.scheduleReconnect(); };
      ws.onerror = () => ws.close();
    },

    scheduleReconnect() {
      const delay = Math.min(5000, 300 * 2 ** this.retry++);
      setTimeout(() => this.connect(), delay);
    },

    send(msg) {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
      else if (msg.type !== "video_time") this.outbox.push(msg);   // don't queue stale clock ticks
    }
  };

  window.Transport = Transport;
  window.App.state.fake = FAKE;
  if (FAKE) setConn("fake", "Fake mode · no server");
  else {
    // real mode: point the webcam <img> tags at Python's /camera stream
    document.querySelectorAll("img.cam-feed").forEach((img) => { img.src = img.dataset.src; });
    Transport.connect();
  }
})();
