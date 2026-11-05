from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>实时字幕</title>
<style>
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; width: 100%; height: 100%;
    background: transparent; overflow: hidden;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  body.preview { background: radial-gradient(circle at 50% 40%, #2a3140, #10141c 70%); }
  #stage {
    position: fixed; left: 0; right: 0; bottom: 6vh;
    display: flex; flex-direction: column; align-items: center; justify-content: flex-end;
    gap: 0.35em; padding: 0 4vw; pointer-events: none;
  }
  .line {
    max-width: 92vw; text-align: center; line-height: 1.22;
    color: #ffffff; font-weight: 800; letter-spacing: 0.02em;
    text-shadow:
      0 0 0.06em #000, 0 0 0.12em #000, 0 0 0.25em rgba(0,0,0,.9),
      0.035em 0.035em 0 #000, -0.035em 0.035em 0 #000,
      0.035em -0.035em 0 #000, -0.035em -0.035em 0 #000;
    opacity: 0; transform: translateY(4px);
    transition: opacity .15s ease, transform .15s ease;
    word-break: break-word;
  }
  .line.show { opacity: 1; transform: translateY(0); }
  body.still .line { transition: none; }
  .line.partial { color: #d9ecff; opacity: .92; font-weight: 700; }
  .line.partial::after {
    content: ""; display: inline-block; width: .08em; height: .95em;
    background: currentColor; margin-left: .1em; vertical-align: -.08em;
    animation: blink 1s steps(1) infinite;
  }
  @keyframes blink { 50% { opacity: 0; } }
</style>
</head>
<body>
<div id="stage">
  <div class="line" id="l1"></div>
</div>
<script>
(function () {
  const params = new URLSearchParams(location.search);
  if (params.has('preview')) document.body.classList.add('preview');
  if (params.has('still')) document.body.classList.add('still');

  const l1 = document.getElementById('l1');

  function layout() {
    const text = l1.textContent;
    const maxW = window.innerWidth * 0.90;
    let size = text
      ? Math.min(window.innerWidth * 0.062, maxW / (text.length * 1.10))
      : window.innerWidth * 0.05;
    size = Math.max(size, window.innerWidth * 0.02);
    l1.style.fontSize = size.toFixed(1) + 'px';
  }

  function setLine(text, partial) {
    if (!text) {
      l1.classList.remove('show');
      setTimeout(() => { if (!l1.classList.contains('show')) l1.textContent = ''; }, 180);
      return;
    }
    if (l1.textContent !== text) l1.textContent = text;
    l1.classList.toggle('partial', !!partial);
    l1.classList.add('show');
  }

  let hideTimer = null;

  function holdMs(s) {
    const sec = Number(s && s.hold ? s.hold : 4);
    return Math.min(15000, Math.max(1500, sec * 1000));
  }

  function render(s) {
    const lines = (s.lines || []).slice(-1);
    const last = lines.length ? lines[0] : '';
    if (s.partial) {
      clearTimeout(hideTimer);
      setLine(s.partial, true);
      layout();
      return;
    }
    setLine(last, false);
    layout();
    if (last) {
      clearTimeout(hideTimer);
      hideTimer = setTimeout(() => setLine('', false), holdMs(s));
    }
  }

  function connect() {
    const es = new EventSource('/events');
    es.onmessage = (e) => {
      try { render(JSON.parse(e.data)); } catch (_) {}
    };
    es.onerror = () => { es.close(); setTimeout(connect, 1000); };
  }
  connect();
  window.addEventListener('resize', layout);
})();
</script>
</body>
</html>
"""

class SubtitleServer:
    def __init__(self, port: int = 8765):
        self.port = port
        self.hold = 4.0
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Condition()
        self._state = {"lines": [], "partial": "", "v": 0}
        self._closed = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> bool:
        if self._httpd is not None:
            return True
        handler = self._make_handler()
        last_err = None
        for p in range(self.port, self.port + 12):
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", p), handler)
                httpd.daemon_threads = True
                self._httpd = httpd
                self.port = p
                break
            except OSError as e:
                last_err = e
                continue
        if self._httpd is None:
            self._last_error = last_err
            return False
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="web", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.notify_all()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def update(self, lines: list[str], partial: str) -> None:
        with self._lock:
            self._state = {
                "lines": list(lines),
                "partial": partial or "",
                "hold": float(self.hold),
                "v": self._state["v"] + 1,
            }
            self._lock.notify_all()

    def _snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args) -> None:
                pass

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except Exception:
                    pass

            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                if path in ("/", "/index.html"):
                    self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                elif path == "/state":
                    body = json.dumps(outer._snapshot(), ensure_ascii=False).encode("utf-8")
                    self._send(200, body, "application/json; charset=utf-8")
                elif path == "/events":
                    self._events()
                elif path == "/health":
                    self._send(200, b"ok", "text/plain")
                else:
                    self._send(404, b"not found", "text/plain")

            def _events(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                last = -1
                try:
                    while True:
                        with outer._lock:
                            if outer._closed:
                                return
                            if outer._state["v"] == last:
                                outer._lock.wait(timeout=10)
                            if outer._closed:
                                return
                            if outer._state["v"] != last:
                                last = outer._state["v"]
                                data = json.dumps(outer._state, ensure_ascii=False)
                            else:
                                data = None
                        if data is None:
                            self.wfile.write(b": ping\n\n")
                        else:
                            self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                        self.wfile.flush()
                except Exception:
                    return

        return Handler

def find_free_port(start: int = 8765) -> int:
    for p in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start
