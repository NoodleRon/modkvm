"""Canceled synthetic requests must release resources without server tracebacks."""
import socket
import struct
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

from test_remote_camera import pb


class DisconnectTests(unittest.TestCase):
    def server(self, handler):
        errors = []
        class Server(pb.ThreadingHTTPServer):
            daemon_threads = True
            def handle_error(self, request, address): errors.append(sys.exc_info()[1])
            def process_request_thread(self, request, address):
                try: super().process_request_thread(request,address)
                finally: self.completed.set()
        srv = Server(("127.0.0.1", 0), handler)
        srv.completed=threading.Event()
        thread = threading.Thread(target=lambda: srv.serve_forever(poll_interval=.01), daemon=True)
        thread.start()
        def close(): srv.shutdown(); srv.server_close(); thread.join(1)
        self.addCleanup(close)
        return srv, errors

    def cancel(self, srv, path, started, resume, token="test-console"):
        client = socket.create_connection(srv.server_address, timeout=2)
        client.sendall(f"GET {path} HTTP/1.0\r\nHost: localhost\r\nAuthorization: Bearer {token}\r\n\r\n".encode())
        self.assertTrue(started.wait(2))
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        client.close()  # reset before a delayed status reply is written
        resume.set()

    def test_canceled_gateway_status_reply_is_quiet(self):
        started,resume,finished = threading.Event(),threading.Event(),threading.Event()
        def status(): started.set(); resume.wait(2); return {"status": {"devices": []}}
        class Handler(pb.WebHandler):
            def finish(self):
                try: super().finish()
                finally: finished.set()
        with patch.object(pb.Gateway,"token","test-console"), patch.object(pb,"hub_status",status):
            srv,errors=self.server(Handler)
            self.cancel(srv,"/hub",started,resume)
            self.assertTrue(finished.wait(2))
            self.assertTrue(srv.completed.wait(2))
            self.assertEqual(errors,[])

    def test_canceled_camera_list_reply_is_quiet(self):
        started,resume,finished = threading.Event(),threading.Event(),threading.Event()
        def describe(): started.set(); resume.wait(2); return []
        class Handler(pb.SeeHandler):
            def finish(self):
                try: super().finish()
                finally: finished.set()
        with patch.object(pb.State,"token","test-console"), patch.object(pb.State,"reg",types.SimpleNamespace(describe=describe)):
            srv,errors=self.server(Handler)
            self.cancel(srv,"/cams",started,resume)
            self.assertTrue(finished.wait(2))
            self.assertTrue(srv.completed.wait(2))
            self.assertEqual(errors,[])

    def test_disconnected_stream_releases_viewer_even_without_frames(self):
        # Cam lifecycle is exercised, while capture, discovery and subprocesses are forbidden.
        cam=pb.Cam("fixture","test:",{},{});released=threading.Event()
        original_release=cam.release
        def release(): original_release(); released.set()
        with patch.object(cam,"_ensure"), patch.object(cam,"release",release), patch.object(pb.State,"token","test-console"), patch.object(pb.State,"reg",types.SimpleNamespace(get=lambda name:cam)), patch.object(pb.subprocess,"Popen",side_effect=AssertionError("no capture")):
            srv,errors=self.server(pb.SeeHandler)
            client=socket.create_connection(srv.server_address,timeout=2)
            client.sendall(b"GET /stream/fixture HTTP/1.0\r\nHost: localhost\r\nAuthorization: Bearer test-console\r\n\r\n")
            self.assertIn(b"200",client.recv(4096))
            client.shutdown(socket.SHUT_RDWR);client.close()
            try:
                self.assertTrue(released.wait(2),"canceled camera stream retained its viewer while no frames arrived")
                self.assertEqual(cam.viewers,0);self.assertEqual(errors,[])
            finally:
                # Release an unfixed handler too, so a failing baseline leaves no waiting worker.
                with cam.cond: cam.latest=b"invalid fixture";cam.seq+=1;cam.cond.notify_all()

    def test_canceled_gateway_stream_releases_an_idle_upstream_viewer(self):
        cam=pb.Cam("fixture","test:",{},{});released=threading.Event()
        original_release=cam.release
        def release(): original_release();released.set()
        with patch.object(cam,"_ensure"), patch.object(cam,"release",release), patch.object(pb.State,"token","test-console"), patch.object(pb.Gateway,"token","test-console"), patch.object(pb,"sb_token",return_value="test-console"), patch.object(pb.State,"reg",types.SimpleNamespace(get=lambda name:cam)), patch.object(pb.subprocess,"Popen",side_effect=AssertionError("no capture")):
            backend,backend_errors=self.server(pb.SeeHandler)
            with patch.object(pb,"SB_URL","http://127.0.0.1:"+str(backend.server_port)):
                gateway,errors=self.server(pb.WebHandler)
                client=socket.create_connection(gateway.server_address,timeout=2)
                client.sendall(b"GET /stream/fixture HTTP/1.0\r\nHost: localhost\r\nAuthorization: Bearer test-console\r\n\r\n")
                self.assertIn(b"200",client.recv(4096))
                client.shutdown(socket.SHUT_RDWR);client.close()
                try:
                    self.assertTrue(released.wait(2),"gateway kept an abandoned upstream viewer while waiting for a frame")
                    self.assertEqual(cam.viewers,0);self.assertEqual(errors,[]);self.assertEqual(backend_errors,[])
                finally:
                    with cam.cond: cam.latest=b"invalid fixture";cam.seq+=1;cam.cond.notify_all()

    def test_key_api_does_not_retry_a_canceled_reply_as_a_500(self):
        # Obtain the actual handler without creating a Key app, socket, or Bluetooth session.
        srv=types.SimpleNamespace(serve_forever=lambda:None)
        def factory(address,handler): srv.RequestHandlerClass=handler;return srv
        app=types.SimpleNamespace(api_lock=threading.RLock(),api=lambda *a:{})
        with patch.object(pb,"ThreadingHTTPServer",side_effect=factory): pb.make_api(app,"fixture","127.0.0.1",0)
        handler=srv.RequestHandlerClass.__new__(srv.RequestHandlerClass)
        handler.path="/status";handler._auth=lambda:True
        handler._out=Mock(side_effect=BrokenPipeError("synthetic canceled response"))
        handler._run("GET")
        handler._out.assert_called_once()

    def test_unexpected_handler_errors_are_still_reported(self):
        class Handler(pb.WebHandler):
            def do_GET(self): raise ValueError("synthetic application failure")
        srv,errors=self.server(Handler)
        client=socket.create_connection(srv.server_address,timeout=2)
        client.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
        self.assertEqual(client.recv(4096),b"");client.close()
        self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],ValueError)


if __name__ == "__main__": unittest.main()
