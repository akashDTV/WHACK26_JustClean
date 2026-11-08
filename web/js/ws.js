
(function () {
    const params = new URLSearchParams(location.search);

    // Fake mode for testing without Python.
    const FAKE = params.has("fake") || location.protocol === "file:";

    const WS_URL = params.get("ws") ||
        `${location.protocol === "https:" ? "wss" : "ws"}://${location.host || "localhost:8000"}/ws`;

    const conn = document.getElementById("conn");

    function setConn(state, text) {
        if (!conn) return;

        conn.dataset.state = state;
        conn.textContent = text;
    }

    const Transport = {
        ws: null,
        retry: 0,
        reconnectTimer: null,
        outbox: [],

        connect() {
            setConn("connecting", "Connecting to the coach…");

            let ws;

            try {
                ws = new WebSocket(WS_URL);
            } catch (error) {
                console.error("[ws] Connection failed:", error);
                this.scheduleReconnect();
                return;
            }

            this.ws = ws;

            ws.onopen = () => {
                console.log("[ws] Connected to Python");

                this.retry = 0;
                setConn("open", "Connected");

                // Ask Python for the current state and available clips.
                this.send({
                    type: "command",
                    name: "hello"
                });

                // Restore the current frontend settings.
                if (window.App) {
                    this.send({
                        type: "command",
                        name: "choose_coach",
                        value: App.state.coachId
                    });

                    this.send({
                        type: "command",
                        name: "mirror",
                        value: App.state.mirror
                    });

                    this.send({
                        type: "command",
                        name: "auto_pause",
                        value: App.state.autoPause
                    });
                }

                // Send queued messages.
                const queued = this.outbox.splice(0);

                queued.forEach((msg) => {
                    if (ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify(msg));
                    }
                });
            };

            ws.onmessage = (event) => {
                let msg;

                try {
                    msg = JSON.parse(event.data);
                } catch (error) {
                    console.warn("[ws] Invalid JSON:", event.data);
                    return;
                }

                if (window.App && typeof App.handle === "function") {
                    App.handle(msg);
                }
            };

            ws.onclose = () => {
                // Ignore an old connection closing after a newer one opened.
                if (this.ws !== ws) return;

                console.warn("[ws] Disconnected");

                setConn("closed", "Lost the coach — reconnecting…");
                this.scheduleReconnect();
            };

            ws.onerror = (error) => {
                console.error("[ws] WebSocket error:", error);
                ws.close();
            };
        },

        scheduleReconnect() {
            if (this.reconnectTimer) return;

            const delay = Math.min(10000, 500 * 2 ** this.retry++);

            console.log(`[ws] Reconnecting in ${delay}ms`);

            this.reconnectTimer = setTimeout(() => {
                this.reconnectTimer = null;
                this.connect();
            }, delay);
        },

        send(msg) {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify(msg));
            } else if (msg.type !== "video_time") {
                // Never queue outdated video timestamps.
                this.outbox.push(msg);
            }
        }
    };

    window.Transport = Transport;

    if (window.App) {
        App.state.fake = FAKE;
    }

    if (FAKE) {
        setConn("fake", "Fake mode · no server");
        console.log("[ws] Running in fake mode");
    } else {
        Transport.connect();
    }
})();
