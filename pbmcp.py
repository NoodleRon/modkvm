#!/usr/bin/env python3
"""Portabotty MCP over stdio: thin wrapper over the PBKEY and PBSEE HTTP APIs (no phone or camera logic here).
The hub (`pb`) already serves the same tools over HTTP at /mcp; use this file only for clients that need stdio.

  pbkey.py  (input,  http://127.0.0.1:8809, token file .pbkey_token next to pbkey.py)
  pbsee.py  (output, http://127.0.0.1:8808)

Register (Claude Code):  claude mcp add portabotty -- python3 /path/to/pbmcp.py
Env: PBKEY_URL, PBKEY_TOKEN | PBKEY_TOKEN_FILE, PBSEE_URL, PBSEE_TOKEN, PBSEE_CAM (default camera name)
Standard library only.
"""
import os, sys, json, time, base64, urllib.request, urllib.error, urllib.parse

KB_URL = os.environ.get("PBKEY_URL", "http://127.0.0.1:8809").rstrip("/")
CV_URL = os.environ.get("PBSEE_URL", "http://127.0.0.1:8808").rstrip("/")
CV_TOKEN = os.environ.get("PBSEE_TOKEN", "")
CV_CAM = os.environ.get("PBSEE_CAM", "")


def kb_token():
    t = os.environ.get("PBKEY_TOKEN")
    if t: return t.strip()
    for path in (os.environ.get("PBKEY_TOKEN_FILE"), os.path.expanduser("~/.pbkey_token"),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pbkey_token")):
        if path:
            try: return open(path).read().strip()
            except OSError: pass
    return ""


def http(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                 headers=headers or {}, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        body = e.read()
        try: msg = json.loads(body).get("error", body.decode(errors="replace"))
        except Exception: msg = body.decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {msg}")
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"cannot reach {url}: {getattr(e, 'reason', e)}")


def kb(path, data=None):
    raw, _ = http(KB_URL + path, data if data is not None else ({} if path != "/status" else None),
                  {"Authorization": "Bearer " + kb_token(), "Content-Type": "application/json"})
    return json.loads(raw)


def cv_headers():
    return {"Authorization": "Bearer " + CV_TOKEN} if CV_TOKEN else {}


def cv_json(path):
    raw, _ = http(CV_URL + path, None, cv_headers())
    return json.loads(raw)


def linked_cam():
    """Camera linked (web console) to the active phone, else ''."""
    try:
        cfg = json.load(open(os.environ.get("PORTABOTTY_CONFIG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "portabotty.json"))))
        return cfg.get("links", {}).get(kb("/status").get("active"), "")
    except Exception:
        return ""


def pick_cam(name):
    if name or CV_CAM: return name or CV_CAM
    c = linked_cam()
    if c: return c
    cams = cv_json("/cams")
    cams = cams.get("cams", cams) if isinstance(cams, dict) else cams
    names = [c if isinstance(c, str) else c.get("name") for c in cams]
    if not names: raise RuntimeError("pbsee reports no cameras")
    return names[0]


FR = {"type": "number", "minimum": 0, "maximum": 1}
TOOLS = [
    ("phone_status", "List paired phones, which is active/connected, and mouse state.", {}, []),
    ("phone_use", "Switch to a phone (number from phone_status, name fragment, or address). Dials it; no touching the phone.",
     {"device": {"type": ["string", "integer"]}}, ["device"]),
    ("phone_type", "Type text on the phone's focused field. submit=true presses Enter after. ASCII only.",
     {"text": {"type": "string"}, "submit": {"type": "boolean"}}, ["text"]),
    ("phone_key", "Press a key or combo, e.g. 'enter', 'esc', 'cmd+h' (iOS Home), 'cmd+space' (Spotlight), 'cmd+tab', 'ctrl+c'. "
                  "Pass 'keys' for a sequence.", {"key": {"type": "string"}, "keys": {"type": "array", "items": {"type": "string"}}}, []),
    ("phone_tap", "Click at a position given as fractions of the phone screen (x,y in 0..1; 0,0 = top-left). "
                  "Needs AssistiveTouch on the phone. Pointer position is estimated, so verify with phone_look.",
     {"x": FR, "y": FR, "button": {"enum": ["left", "right", "middle"]}, "double": {"type": "boolean"}}, ["x", "y"]),
    ("phone_scroll", "Scroll at an optional position.",
     {"dir": {"enum": ["up", "down"]}, "amount": {"type": "integer"}, "x": FR, "y": FR}, ["dir"]),
    ("phone_drag", "Press at (x1,y1), drag to (x2,y2), release. Fractions of the screen. Use for swipes.",
     {"x1": FR, "y1": FR, "x2": FR, "y2": FR, "duration": {"type": "number"}}, ["x1", "y1", "x2", "y2"]),
    ("phone_home_pointer", "Re-home the pointer to the top-left corner (fixes drift after many moves).", {}, []),
    ("phone_look", "Take a photo of the phone screen from the webcam (pbsee; uses the camera linked to the active phone in the web console). Optionally wait `settle` seconds first so the "
                   "screen has updated. Returns the image plus the change score.",
     {"settle": {"type": "number"}, "camera": {"type": "string"}, "query": {"type": "string",
      "description": "extra pbsee options, e.g. 'w=900&contrast'"}}, []),
]


def tool_call(name, a):
    if name == "phone_status":
        return [text(json.dumps(kb("/status"), indent=1))]
    if name == "phone_use": return [text(json.dumps(kb("/use", {"device": a["device"]})))]
    if name == "phone_type": return [text(json.dumps(kb("/type", {"text": a["text"], "submit": bool(a.get("submit"))})))]
    if name == "phone_key":
        if not (a.get("key") or a.get("keys")): raise RuntimeError("give key or keys")
        return [text(json.dumps(kb("/key", {"keys": a["keys"]} if a.get("keys") else {"key": a["key"]})))]
    if name == "phone_tap": return [text(json.dumps(kb("/click", a)))]
    if name == "phone_scroll": return [text(json.dumps(kb("/scroll", a)))]
    if name == "phone_drag": return [text(json.dumps(kb("/drag", a)))]
    if name == "phone_home_pointer": return [text(json.dumps(kb("/home", {})))]
    if name == "phone_look":
        if a.get("settle"): time.sleep(min(float(a["settle"]), 10))
        cam = pick_cam(a.get("camera"))
        q = ("?" + a["query"].lstrip("?")) if a.get("query") else ""
        img, ctype = http(f"{CV_URL}/snap/{urllib.parse.quote(cam)}.jpg{q}", None, cv_headers(), timeout=30)
        info = ""
        try: info = json.dumps(cv_json("/status"))[:600]
        except Exception: pass
        return [{"type": "image", "data": base64.b64encode(img).decode(), "mimeType": ctype or "image/jpeg"},
                text(f"camera={cam} {info}")]
    raise RuntimeError(f"unknown tool {name}")


def text(t): return {"type": "text", "text": t}


def handle(m):
    """One JSON-RPC message -> reply dict (None for notifications). Used by stdio and by the hub's HTTP /mcp."""
    method, i, p = m.get("method"), m.get("id"), m.get("params") or {}
    if i is None: return None                                    # notifications need no reply
    def ok(result): return {"jsonrpc": "2.0", "id": i, "result": result}
    if method == "initialize":
        return ok({"protocolVersion": p.get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}},
                   "serverInfo": {"name": "portabotty", "version": "1.0"},
                   "instructions": "Drive an iPhone/iPad/computer over Bluetooth (keyboard+pointer) and look at it through a webcam. "
                                   "Loop: act (phone_type/key/tap) -> phone_look with settle=1 -> decide. "
                                   "The screen may show passwords; never type secrets you were not given."})
    if method == "ping": return ok({})
    if method == "tools/list":
        return ok({"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": pr, "required": req}}
                             for n, d, pr, req in TOOLS]})
    if method == "tools/call":
        try: return ok({"content": tool_call(p.get("name"), p.get("arguments") or {})})
        except Exception as e: return ok({"content": [text(str(e))], "isError": True})
    return {"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve():
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        try: m = json.loads(line)
        except ValueError: continue
        r = handle(m)
        if r is not None:
            sys.stdout.write(json.dumps(r) + "\n"); sys.stdout.flush()


if __name__ == "__main__":
    serve()
