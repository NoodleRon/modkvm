"""Synthetic-only regression tests. Never instantiate Key, discover devices or run ffmpeg.

Run: python3 -m unittest discover -s tests -v (Pillow/numpy, the normal PBSEE dependencies).
BlueZ/GLib are stubbed only for import, so these tests also run on macOS.
"""
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def load_app():
    dbus = types.ModuleType("dbus")
    service = types.ModuleType("dbus.service")
    service.Object = object
    service.method = lambda *a, **kw: lambda f: f
    dbus.service = service
    dbus.mainloop = types.ModuleType("dbus.mainloop")
    dbus.mainloop.glib = types.ModuleType("dbus.mainloop.glib")
    gi = types.ModuleType("gi")
    gi.repository = types.ModuleType("gi.repository")
    gi.repository.GLib = types.SimpleNamespace()
    stubs = {"dbus": dbus, "dbus.service": service, "dbus.mainloop": dbus.mainloop,
             "dbus.mainloop.glib": dbus.mainloop.glib, "gi": gi, "gi.repository": gi.repository}
    spec = importlib.util.spec_from_file_location("portabotty_test", ROOT / "portabotty.py")
    app = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs): spec.loader.exec_module(app)
    return app


pb = load_app()
relay_spec = importlib.util.spec_from_file_location("relay_test", ROOT / "relay.py")
relay = importlib.util.module_from_spec(relay_spec)
relay_spec.loader.exec_module(relay)


def jpeg(size=(96, 64), color="blue"):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG")
    return out.getvalue()


class RemoteCameraTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, value in (("SEE_CONFIG", str(Path(self.tmp.name) / "see.json")),
                              ("WEB_CONFIG", str(Path(self.tmp.name) / "web.json"))):
            p = patch.object(pb, target, value); p.start(); self.addCleanup(p.stop)
        for name in ("discover", "usb_video_devices"):
            p = patch.object(pb, name, return_value=[]); p.start(); self.addCleanup(p.stop)
        p = patch.object(pb.subprocess, "Popen", side_effect=AssertionError("no process/camera capture in tests"))
        p.start(); self.addCleanup(p.stop)
        self.cfg = {"cameras": [], "remote_cameras": True}
        self.reg = pb.Registry(self.cfg, test_source=True)
        self.addCleanup(self.reg.shutdown)
        self.reg.refresh()
        pb.State.reg, pb.State.token = self.reg, "test-camera-backend"
        p = patch.object(pb, "sb_token", return_value="test-camera-backend"); p.start(); self.addCleanup(p.stop)
        pb.Gateway.token = "test-console"
        pb.FAILS.clear()
        self.backend = self.serve(pb.SeeHandler)
        p = patch.object(pb, "SB_URL", self.backend); p.start(); self.addCleanup(p.stop)
        self.gateway = self.serve(pb.WebHandler)
        self.frame = jpeg()

    def serve(self, handler):
        server = pb.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
        thread.start()
        def close():
            server.shutdown(); server.server_close(); thread.join(1)
        self.addCleanup(close)
        return "http://127.0.0.1:" + str(server.server_port)

    def request(self, path, body=None, token="test-console", headers=None, base=None):
        hdr = {"Authorization": "Bearer " + token}
        if isinstance(body, (dict, list)):
            hdr["Content-Type"] = "application/json"
            body = json.dumps(body).encode()
        hdr.update(headers or {})
        req = urllib.request.Request((base or self.gateway) + path, data=body, headers=hdr)
        try: r = urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as e: r = e
        with r: return r.status, r.read(), r.headers

    def start(self, label="Laptop camera"):
        status, body, _ = self.request("/remote/start", {"label": label})
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def upload(self, session, frame=None, **kwargs):
        return self.request("/remote/frame/" + session["name"], self.frame if frame is None else frame,
                            headers={"Content-Type": "image/jpeg", "X-PB-Camera": session["token"]}, **kwargs)

    def test_disabled_by_default_and_flag_does_not_persist(self):
        empty = {"cameras": []}
        disabled, enabled = pb.Registry(empty), pb.Registry(empty, allow_remote_cameras=True)
        self.addCleanup(disabled.shutdown); self.addCleanup(enabled.shutdown)
        with self.assertRaises(pb.ApiError): disabled.start_remote("Laptop")
        enabled.start_remote("Laptop")
        self.assertNotIn("remote_cameras", empty)
        self.reg.remote_enabled = False
        self.assertEqual(self.request("/remote/start", {"label": "No"})[0], 403)

    def test_served_version_and_share_entry_are_visible_without_capture(self):
        status, body, _ = self.request("/version")
        self.assertEqual(status, 200)
        version = json.loads(body)
        self.assertEqual(version["build"], pb.BUILD)
        self.assertEqual(version["features"]["remote_camera_sharing"], 1)
        self.assertEqual(self.request("/version", token="wrong")[0], 401)
        self.reg.remote_enabled = False
        status, body, _ = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'Share this computer', body)
        self.assertIn(b'Link / Camera', body)
        self.assertIn(pb.BUILD.encode(), body)
        self.assertNotIn(b'__PB_BUILD__', body)
        self.assertEqual(self.reg.status()["build"], pb.BUILD)

    def test_hub_distinguishes_old_backend_from_disabled_remote_sharing(self):
        with patch.object(pb, "port_open", side_effect=lambda url: url == pb.SB_URL):
            with patch.object(pb, "http_json", return_value={"cams": []}):
                self.assertFalse(pb.hub_status()["remote_camera_support"])
            with patch.object(pb, "http_json", return_value={"cams": [], "remote_cameras": False, "build": "synthetic-backend"}):
                status = pb.hub_status()
                self.assertTrue(status["remote_camera_support"])
                self.assertFalse(status["remote_cameras"])
                self.assertEqual(status["camera_build"], "synthetic-backend")
            with patch.object(pb, "http_json", side_effect=OSError("synthetic status failure")):
                self.assertIsNone(pb.hub_status()["remote_camera_support"])

    def test_auth_origin_and_transport_required(self):
        self.assertEqual(self.request("/remote/start", {"label": "No"}, token="wrong")[0], 401)
        self.assertEqual(self.request("/remote/start", {"label": "No"}, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("/remote/start", {"label": "No"}, headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request("/remote/start", b'{"label":"No"}', headers={"Content-Type": "text/plain"})[0], 415)
        peer = types.SimpleNamespace(client_address=("192.0.2.1", 1), headers={"X-Forwarded-Proto": "https"})
        self.assertFalse(pb.remote_request_ok(peer))
        peer.client_address = ("127.0.0.1", 1)
        peer.headers = {"Origin": "https://console.example", "Host": "console.example"}
        self.assertTrue(pb.remote_request_ok(peer))
        self.assertEqual(self.request("/remote/start", {"label": "No"}, token="wrong", base=self.backend)[0], 401)

    def test_shared_camera_is_in_existing_list_and_local_camera_survives(self):
        session = self.start()
        cam = self.reg.get(session["name"])
        self.assertFalse(cam.present)
        cam.acquire(); cam.touch(); cam.release()
        self.assertIsNone(cam.thread)
        self.assertEqual(self.upload(session)[0], 200)
        self.reg.refresh(force=True)
        status, data, _ = self.request("/cams")
        self.assertEqual(status, 200)
        rows = json.loads(data)
        self.assertEqual({c["source"] for c in rows}, {"local", "remote"})
        self.assertTrue(next(c for c in rows if c["name"] == session["name"])["present"])
        self.assertTrue(self.reg.get("test").present)
        self.assertNotIn(session["token"], data.decode())
        self.assertNotIn(session["token"], json.dumps(self.reg.status()))
        self.assertFalse(cam.focus(True)["ok"])

    def test_frame_capability_is_scoped_and_bounded(self):
        a, b = self.start("A"), self.start("B")
        self.assertEqual(self.upload({"name": a["name"], "token": b["token"]})[0], 403)
        self.assertEqual(self.upload(a, b"not a JPEG")[0], 400)
        self.assertEqual(self.upload(a, jpeg((2000, 1200)))[0], 400)
        self.assertEqual(self.request("/remote/frame/" + a["name"], b"x", headers={
            "Content-Type": "image/jpeg", "X-PB-Camera": a["token"], "Content-Length": str(pb.REMOTE_MAX_FRAME + 1)})[0], 413)
        self.assertEqual(self.upload(a)[0], 200)
        self.assertEqual(self.upload(a)[0], 429)
        self.assertEqual(self.reg.get(a["name"]).latest, self.frame)

    def test_stop_releases_images_and_revokes_token(self):
        session = self.start()
        self.upload(session)
        self.assertEqual(self.request("/remote/stop/" + session["name"], {})[0], 200)
        cam = self.reg.get(session["name"])
        self.assertFalse(cam.present)
        self.assertIsNone(cam.latest)
        self.assertFalse(cam.publisher)
        self.assertEqual(self.upload(session)[0], 403)
        self.assertEqual(self.request("/snap/" + session["name"])[0], 503)
        restarted = self.start()
        self.assertEqual(restarted["name"], session["name"])
        self.assertNotEqual(restarted["token"], session["token"])
        self.assertEqual(self.upload(session)[0], 403)

    def test_disconnect_expires_without_a_viewer_or_poll(self):
        session = self.start()
        self.upload(session)
        cam = self.reg.get(session["name"])
        with cam.cond:
            cam.deadline = time.monotonic() - 1
            self.assertTrue(cam.cond.wait_for(lambda: cam.latest is None, timeout=2))
        self.assertFalse(cam.present)
        self.assertEqual(self.upload(session)[0], 403)

    def test_existing_link_calibration_and_snapshot_use_remote_provider(self):
        session = self.start()
        name = session["name"]
        self.upload(session)
        status, _, _ = self.request("/links", {"addr": "synthetic-target", "cam": name})
        self.assertEqual(status, 200)
        self.assertEqual(pb.load_cfg()["links"]["synthetic-target"], name)
        with patch.object(pb, "kb", return_value={"active": "synthetic-target"}), patch.object(pb, "DEFAULT_CAM", ""):
            self.assertEqual(pb.pick_cam(""), name)
            self.assertEqual(pb.pick_cam(name), name)
        self.assertEqual(self.request("/calibrate/" + name, {"warp": [0, 0, 1, 0, 1, 1, 0, 1]})[0], 200)
        self.assertEqual(self.request("/save/" + name, {"rot": 90, "w": 48})[0], 200)
        timer = threading.Timer(.15, lambda: self.upload(session))
        timer.start(); self.addCleanup(timer.join)
        status, body, headers = self.request("/snap/" + name + "?avg=1")
        self.assertEqual(status, 200, body[:100])
        self.assertEqual(headers.get("Content-Type"), "image/jpeg")
        with Image.open(io.BytesIO(body)) as im: self.assertEqual(im.size, (48, 72))
        saved = json.loads(Path(pb.SEE_CONFIG).read_text())
        self.assertNotIn(session["token"], json.dumps(saved))
        restored = pb.Registry(saved)
        self.addCleanup(restored.shutdown)
        self.assertFalse(restored.get(name).present)
        self.assertEqual(restored.get(name).defaults()["rot"], 90)
        self.assertEqual(restored.start_remote("Laptop camera")["name"], name)

    def test_stream_closes_after_stop(self):
        session = self.start()
        self.upload(session)
        req = urllib.request.Request(self.gateway + "/stream/" + session["name"], headers={"Authorization": "Bearer test-console"})
        with urllib.request.urlopen(req, timeout=3) as stream:
            self.assertIn("multipart", stream.headers.get("Content-Type"))
            self.assertIn(b"--frame", stream.read(32))
            self.request("/remote/stop/" + session["name"], {})
            self.assertLess(len(stream.read()), 5000)
        self.assertEqual(self.reg.get(session["name"]).viewers, 0)

    def test_stop_during_snapshot_does_not_return_a_cached_frame(self):
        session = self.start()
        self.upload(session)
        original = pb.Denoiser.add
        def stop_after_sample(denoiser, frame):
            image = original(denoiser, frame)
            self.reg.get(session["name"]).shutdown()
            return image
        timer = threading.Timer(.15, lambda: self.upload(session))
        timer.start(); self.addCleanup(timer.join)
        with patch.object(pb.Denoiser, "add", stop_after_sample):
            self.assertEqual(self.request("/snap/" + session["name"])[0], 503)

    def test_registry_shutdown_clears_and_disables_remote_sources(self):
        session = self.start()
        self.upload(session)
        self.reg.shutdown()
        self.assertIsNone(self.reg.get(session["name"]).latest)
        self.assertEqual(self.upload(session)[0], 403)
        self.assertEqual(self.request("/remote/start", {"label": "No"})[0], 403)

    def test_malformed_starts_conflicts_and_limits(self):
        for data in ({}, {"label": " "}, {"label": 123}, [], {"label": "x" * 61}):
            self.assertEqual(self.request("/remote/start", data)[0], 400)
        self.start("A")
        self.assertEqual(self.request("/remote/start", {"label": "A"})[0], 409)
        for label in ("B", "C", "D"): self.start(label)
        self.assertEqual(self.request("/remote/start", {"label": "E"})[0], 409)

    def test_keyboard_gateway_and_view_only_unchanged_with_mock_input_backend(self):
        calls = []
        class MockKey(pb.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                calls.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(200); self.send_header("Content-Length", "11"); self.end_headers(); self.wfile.write(b'{"ok":true}')
        key = self.serve(MockKey)
        with patch.object(pb, "KB_URL", key):
            self.start()
            self.assertEqual(self.request("/kb/type", {"text": "synthetic"})[0], 200)
            self.assertEqual(calls, [("/type", {"text": "synthetic"})])
            self.assertEqual(self.request("/viewonly", {"on": True})[0], 200)
            self.assertEqual(self.request("/kb/type", {"text": "blocked"})[0], 403)
            self.assertEqual(len(calls), 1)

    def test_relay_forwards_session_capability_on_existing_channel(self):
        relay.TOKEN, relay.AGENT = "relay-browser", "relay-agent"
        relay.FAILS.clear(); relay.LAST_SEEN[0] = 0
        url = self.serve(relay.H)
        agent = pb.relay_agent(url, "relay-agent", int(self.gateway.rsplit(":", 1)[1]), "test-console")
        self.addCleanup(agent.stop)
        deadline = time.monotonic() + 2
        while not relay.LAST_SEEN[0] and time.monotonic() < deadline: threading.Event().wait(.01)
        self.assertTrue(relay.LAST_SEEN[0])
        status, data, _ = self.request("/remote/start", {"label": "Relay camera"}, token="relay-browser", base=url)
        self.assertEqual(status, 200, data)
        session = json.loads(data)
        self.assertEqual(self.upload(session, token="relay-browser", base=url)[0], 200)
        self.assertEqual(self.reg.get(session["name"]).latest, self.frame)
        self.assertEqual(self.request("/remote/stop/" + session["name"], {}, token="relay-browser", base=url)[0], 200)

    def test_plain_public_relay_is_rejected_without_contacting_any_server(self):
        done = threading.Event()
        heads = []
        sent_job = []
        job = {"id": "test", "method": "POST", "path": "/remote/start", "query": "",
               "ctype": "application/json", "body": "e30="}
        class Reply(io.BytesIO):
            status = 200
        def fake_urlopen(req, **kwargs):
            if req.full_url.endswith("/_a/next"):
                if sent_job: raise OSError("no more synthetic jobs")
                sent_job.append(True)
                return Reply(json.dumps(job).encode())
            if req.full_url.endswith("/head"): heads.append(json.loads(req.data))
            elif req.full_url.endswith("/end"): done.set()
            elif not req.full_url.endswith("/data"): self.fail("unexpected forwarding")
            return Reply()
        with patch.object(pb.urllib.request, "urlopen", side_effect=fake_urlopen):
            agent = pb.relay_agent("http://uncontacted.example.invalid", "fictional-token", 1)
            try: self.assertTrue(done.wait(2))
            finally: agent.stop()
        self.assertEqual(heads[0]["status"], 403)

    def test_relay_refuses_remote_capture_on_a_public_plain_http_peer(self):
        class PublicPeer(relay.H):
            def _browser(self, method):
                self.client_address = ("192.0.2.10", 1)  # simulated peer, still a loopback-only test socket
                return super()._browser(method)
        relay.TOKEN = "relay-browser"
        url = self.serve(PublicPeer)
        self.assertEqual(self.request("/remote/start", {"label": "No"}, token="relay-browser", base=url)[0], 403)


if __name__ == "__main__": unittest.main()
