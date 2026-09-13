import json
import os
from pathlib import Path
import runpy
import selectors
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "bin/omaherdr-helper"
DAEMON = ROOT / "bin/omaherdr-daemon"


class LiveOnlyServer:
    """Create a workspace after a snapshot is taken, with no event replay."""

    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "herdr.sock")
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(self.path)
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.stopping = threading.Event()
        self.subscribers = []
        self.error = None
        self.created = False
        self.state = {
            "workspaces": [{"workspace_id": "w1"}],
            "panes": [{"pane_id": "w1:p1"}],
        }
        self.thread = threading.Thread(target=self.serve)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.stopping.set()
        self.listener.close()
        self.thread.join(timeout=2)
        for subscriber in self.subscribers:
            subscriber.close()
        self.directory.cleanup()
        if self.thread.is_alive():
            raise RuntimeError("test server did not stop")
        if self.error is not None and exc_type is None:
            raise self.error

    def serve(self):
        try:
            while not self.stopping.is_set():
                try:
                    connection, _ = self.listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self.stopping.is_set():
                        return
                    raise
                retained = False
                try:
                    connection.settimeout(2)
                    with connection.makefile("rb") as stream:
                        request = json.loads(stream.readline(65536))
                    if request["method"] == "events.subscribe":
                        self.subscribers.append(connection)
                        retained = True
                        result = {"type": "subscription_started"}
                        connection.sendall((json.dumps({"id": request["id"], "result": result}) + "\n").encode())
                    elif request["method"] == "session.snapshot":
                        response = (json.dumps({"id": request["id"], "result": {"snapshot": self.state}}) + "\n").encode()
                        if not self.created:
                            self.created = True
                            self.state["workspaces"].append({"workspace_id": "w2"})
                            self.state["panes"].append({"pane_id": "w2:p1"})
                            event = {"event": "workspace_created", "data": {"workspace_id": "w2"}}
                            for subscriber in self.subscribers:
                                subscriber.sendall((json.dumps(event) + "\n").encode())
                        connection.sendall(response)
                    else:
                        raise AssertionError(request)
                finally:
                    if not retained:
                        connection.close()
        except Exception as error:
            self.error = error


class EventTests(unittest.TestCase):
    def test_workspace_change_during_initial_snapshot_reaches_consumer(self):
        with LiveOnlyServer() as server:
            process = subprocess.Popen(
                [sys.executable, str(HELPER), server.path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            messages = []
            found = False
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    deadline = time.monotonic() + 5
                    buffer = b""
                    while not found and time.monotonic() < deadline:
                        if not selector.select(max(0, deadline - time.monotonic())):
                            break
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            break
                        buffer += chunk
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            message = json.loads(line)
                            messages.append(message)
                            if message.get("kind") == "snapshot":
                                workspaces = message.get("snapshot", {}).get("workspaces", [])
                                found |= any(workspace["workspace_id"] == "w2" for workspace in workspaces)
                            elif message.get("kind") == "event":
                                found |= message.get("data", {}).get("workspace_id") == "w2"
            finally:
                try:
                    _, errors = process.communicate(input=b"quit\n", timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    _, errors = process.communicate()
            self.assertTrue(found, (messages, errors.decode()))

    def test_status_event_updates_bar_counts_without_a_new_snapshot(self):
        daemon = runpy.run_path(str(DAEMON))
        server = daemon["Server"]("local:default", {"host": "local", "session": "default", "windows": []})
        agent = {"pane_id": "w1:p1", "agent": "opencode", "agent_status": "blocked"}
        server.apply_snapshot({"ok": True, "snapshot": {"panes": [dict(agent)], "agents": [dict(agent)]}}, 100)
        server.apply_event({"event": "pane.agent_status_changed", "data": {"pane_id": "w1:p1", "agent_status": "working"}}, 101)
        counts = daemon["counts"]([server])
        self.assertEqual((counts["blocked"], counts["working"]), (0, 1))


if __name__ == "__main__":
    unittest.main()
