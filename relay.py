#!/usr/bin/env python3
"""PBRELAY: public front door for a Portabotty web console behind NAT. Stdlib only; run it on any small host (e.g. AWS).

  python3 relay.py [--port 8080] [--token ACCESS] [--agent-token AGENT]

Your server dials out to it (pbk --relay URL --relay-token AGENT); browsers and MCP clients use URL/?t=ACCESS.
Put TLS in front (Caddy / ALB / nginx). Tokens are generated and printed when not given (env: PBRELAY_TOKEN, PBRELAY_AGENT_TOKEN).
"""
import os, sys, json, time, base64, queue, hmac, secrets, argparse, threading, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

TOKEN = AGENT = ""
INBOX = queue.Queue()          # browser requests waiting for the home agent
REPLY = {}                     # id -> queue of ("head", status, ctype) | ("data", bytes) | ("end",)
LAST_SEEN = [0.0]
LOCK = threading.Lock()


FAILS = {}


def ok(a, b): return bool(a) and hmac.compare_digest(a, b)


def locked_out(ip, limit=8, window=60):
    now = time.time(); FAILS[ip] = [t for t in FAILS.get(ip, []) if now - t < window]
    return len(FAILS[ip]) >= limit


class H(BaseHTTPRequestHandler):
    server_version = "pbrelay"
    def log_message(self, *a): pass

    def _send(self, code, body=b"", ctype="text/plain", extra=()):
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        for k, v in extra: self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)

    def _bearer(self): return self.headers.get("Authorization", "").replace("Bearer ", "")
    def _body(self): return self.rfile.read(min(int(self.headers.get("Content-Length", 0) or 0), 8_000_000))

    # ---- home agent side
    def _agent(self, method):
        ip = self.client_address[0]
        if locked_out(ip): return self._send(429, b"too many wrong tokens, wait a minute")
        if not ok(self._bearer(), AGENT):
            FAILS.setdefault(ip, []).append(time.time()); return self._send(401, b"agent token")
        LAST_SEEN[0] = time.time()
        parts = urllib.parse.urlparse(self.path).path.split("/")[2:]
        if method == "GET" and parts == ["next"]:
            try: r = INBOX.get(timeout=25)
            except queue.Empty: return self._send(204)
            return self._send(200, json.dumps(r).encode(), "application/json")
        if method == "POST" and len(parts) == 2 and parts[1] in ("head", "data", "end"):
            q = REPLY.get(parts[0])
            if q is None: return self._send(410, b"gone")
            body = self._body()
            if parts[1] == "head": j = json.loads(body); q.put(("head", j["status"], j.get("ctype", "application/octet-stream")))
            elif parts[1] == "data": q.put(("data", body))
            else: q.put(("end",))
            return self._send(200)
        self._send(404)

    # ---- browser side
    def _browser(self, method):
        u = urllib.parse.urlparse(self.path); qs = urllib.parse.parse_qs(u.query)
        ip = self.client_address[0]
        if locked_out(ip): return self._send(429, b"too many wrong tokens, wait a minute")
        given = (qs.get("t") or [""])[-1] or self._bearer()
        if not ok(given, TOKEN):
            if given: FAILS.setdefault(ip, []).append(time.time())
            return self._send(401, b"token required (?t=...)")
        if u.path == "/_relay": return self._send(200, json.dumps({"agent": time.time() - LAST_SEEN[0] < 60}).encode(), "application/json")
        if time.time() - LAST_SEEN[0] > 60: return self._send(503, b"home server offline")
        qs.pop("t", None)
        rid = secrets.token_hex(8); q = queue.Queue(); REPLY[rid] = q
        body = self._body() if method == "POST" else b""
        INBOX.put({"id": rid, "method": method, "path": u.path, "query": urllib.parse.urlencode(qs, doseq=True),
                   "ctype": self.headers.get("Content-Type", ""), "body": base64.b64encode(body).decode()})
        try:
            try: head = q.get(timeout=30)
            except queue.Empty: return self._send(504, b"no answer from home server")
            self.send_response(head[1]); self.send_header("Content-Type", head[2]); self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close"); self.end_headers()
            while True:
                m = q.get(timeout=60)
                if m[0] == "end": break
                self.wfile.write(m[1]); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, queue.Empty):
            pass
        finally:
            REPLY.pop(rid, None)

    def _route(self, method):
        if self.path.startswith("/_a/"): return self._agent(method)
        self._browser(method)
    def do_GET(self): self._route("GET")
    def do_POST(self): self._route("POST")


def main():
    global TOKEN, AGENT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8080); ap.add_argument("--listen", default="0.0.0.0")
    ap.add_argument("--token", default=os.environ.get("PBRELAY_TOKEN", ""))
    ap.add_argument("--agent-token", default=os.environ.get("PBRELAY_AGENT_TOKEN", ""))
    a = ap.parse_args()
    TOKEN = a.token or secrets.token_urlsafe(12); AGENT = a.agent_token or secrets.token_urlsafe(18)
    srv = ThreadingHTTPServer((a.listen, a.port), H); srv.daemon_threads = True
    print(f"access:  /?t={TOKEN}\nagent:   --relay URL --relay-token {AGENT}", flush=True)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass


if __name__ == "__main__":
    main()
