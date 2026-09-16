import importlib.util
import os
from pathlib import Path
import runpy
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("omaherdr_attention", ROOT / "bin/omaherdr_attention.py")
ATTENTION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ATTENTION)


class Clock:
    def __init__(self, value=1000.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds

    def set_local(self, hour, minute):
        self.value = time.mktime((2026, 1, 15, hour, minute, 0, 0, 0, -1))


def settings(**changes):
    value = dict(ATTENTION.AttentionEngine.DEFAULT_SETTINGS)
    value.update({"notifications": True})
    value.update(changes)
    return value


def agent(pane, terminal, status, sequence, workspace="w1", tab="t1", name="Agent"):
    return {
        "pane_id": pane,
        "terminal_id": terminal,
        "workspace_id": workspace,
        "tab_id": tab,
        "agent": "opencode",
        "name": name,
        "agent_status": status,
        "state_change_seq": sequence,
    }


def merged(*agents, connected=True, can_open=True, truncated=False):
    workspace_ids = sorted({item["workspace_id"] for item in agents})
    tab_ids = sorted({item["tab_id"] for item in agents})
    state = {
        "servers": [{
            "key": "local:default",
            "host": "local",
            "session": "default",
            "ok": connected,
            "connected": connected,
            "can_open": can_open,
            "snapshot": {
                "workspaces": [{"workspace_id": item, "label": "Workspace " + item} for item in workspace_ids],
                "tabs": [{"tab_id": item, "label": "Tab " + item} for item in tab_ids],
                "panes": [dict(item) for item in agents],
                "agents": [dict(item) for item in agents],
            },
            "since": {item["pane_id"]: 900 for item in agents},
        }]
    }
    if truncated:
        state["truncated"] = True
    return state


def notices(effects):
    return [effect for effect in effects if effect["op"] == "notify"]


class AttentionEngineTests(unittest.TestCase):
    def test_restored_healthy_connection_does_not_alert_while_reconnecting(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            working = agent("p1", "term1", "working", 1)
            first = ATTENTION.AttentionEngine(directory, clock)
            first.update(merged(working, connected=False), settings())
            first.update(merged(working), settings())
            restored = ATTENTION.AttentionEngine(directory, clock)
            restored.update(merged(working, connected=False), settings())
            clock.advance(30)
            self.assertEqual(notices(restored.tick()), [])
            restored.update(merged(working), settings())
            restored.update(merged(working, connected=False), settings())
            clock.advance(8)
            self.assertEqual(len(notices(restored.tick())), 1)

    def test_immediate_bar_event_does_not_notify_with_a_stale_sequence(self):
        daemon = runpy.run_path(str(ROOT / "bin/omaherdr-daemon"))
        server = daemon["Server"]("local:default", {"host": "local", "session": "default", "windows": []})
        clock = Clock()
        with tempfile.TemporaryDirectory() as directory:
            engine = ATTENTION.AttentionEngine(directory, clock)
            initial = merged(agent("p1", "term1", "working", 1))["servers"][0]["snapshot"]
            server.apply_snapshot({"ok": True, "snapshot": initial}, clock())
            server.connected = True
            engine.update(daemon["attention_input"]({server.key: server}), settings())

            server.apply_event({"event": "pane.agent_status_changed", "data": {"pane_id": "p1", "agent_status": "blocked"}}, clock())
            self.assertEqual(daemon["counts"]([server])["blocked"], 1)
            engine.update(daemon["attention_input"]({server.key: server}), settings())
            clock.advance(1)
            self.assertEqual(notices(engine.tick()), [])

            current = merged(agent("p1", "term1", "blocked", 2))["servers"][0]["snapshot"]
            server.apply_snapshot({"ok": True, "snapshot": current}, clock())
            engine.update(daemon["attention_input"]({server.key: server}), settings())
            clock.advance(1)
            effect = notices(engine.tick())[0]
            engine.notification_result(effect["key"], effect["token"], True)
            engine.update(daemon["attention_input"]({server.key: server}), settings())
            self.assertEqual(notices(engine.tick()), [])

    def test_episode_dismissal_does_not_acknowledge_and_open_requires_explicit_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            engine = ATTENTION.AttentionEngine(directory, clock)
            engine.update(merged(agent("p1", "term1", "working", 1)), settings())
            self.assertEqual(engine.tick(), [])

            blocked = agent("p1", "term1", "blocked", 2)
            engine.update(merged(blocked), settings())
            clock.advance(1)
            effect = notices(engine.tick())[0]
            entry = engine.view()["entries"][0]
            self.assertEqual(engine.command("open", entry["id"])["action"], "go")

            engine.notification_closed(effect["key"], 2)
            self.assertEqual(engine.view()["counts"]["blocked"], 1)
            self.assertEqual(engine.tick(), [])

            engine.update(merged(blocked, can_open=False), settings())
            action = engine.command("open", entry["id"])
            self.assertEqual(action["action"], "show_attention")

    def test_each_server_first_usable_snapshot_is_a_suppressed_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            engine = ATTENTION.AttentionEngine(directory, clock)
            engine.update({"servers": []}, settings())
            unresolved = {"servers": [{
                "key": "local:default", "host": "local", "session": "default",
                "ok": False, "connected": False, "can_open": False, "snapshot": None,
            }]}
            engine.update(unresolved, settings())
            clock.advance(9)
            self.assertEqual(engine.tick(), [])

            blocked = agent("p1", "term1", "blocked", 0)
            engine.update(merged(blocked), settings())
            clock.advance(1)
            self.assertEqual(engine.view()["counts"]["blocked"], 1)
            self.assertEqual(engine.tick(), [])

            engine.update(merged(agent("p1", "term1", "working", 0)), settings())
            engine.update(merged(blocked), settings())
            clock.advance(1)
            self.assertEqual(len(notices(engine.tick())), 1)

    def test_process_replacement_reconnect_and_late_result_do_not_replay_an_episode(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            first = ATTENTION.AttentionEngine(directory, clock)
            first.update(merged(agent("p1", "term1", "working", 0)), settings())
            first.update(merged(agent("p1", "term1", "blocked", 0)), settings())
            clock.advance(1)
            original = notices(first.tick())[0]

            replacement = ATTENTION.AttentionEngine(directory, clock)
            same = agent("p1", "term1", "blocked", 0)
            replacement.update(merged(same), settings())
            self.assertEqual(replacement.tick(), [])

            replacement.update({"servers": [], "truncated": True}, settings())
            self.assertTrue(replacement.view()["error"])
            self.assertEqual(replacement.view()["counts"]["blocked"], 1)

            replacement.update(merged(same, connected=False), settings())
            replacement.update(merged(same), settings())
            self.assertEqual(replacement.tick(), [])

            replacement.update(merged(agent("p1", "term1", "working", 0)), settings())
            replacement.update(merged(agent("p1", "term1", "blocked", 0)), settings())
            clock.advance(1)
            current = notices(replacement.tick())[0]
            replacement.notification_result(current["key"], current["token"] - 1, False)
            replacement.notification_result(original["key"], original["token"], False)
            self.assertFalse(replacement.view()["error"])
            self.assertEqual(replacement.tick(), [])

    def test_done_grouping_waits_for_the_window_and_resolved_completions_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            engine = ATTENTION.AttentionEngine(directory, clock)
            a_working = agent("p1", "term1", "working", 1)
            b_working = agent("p2", "term2", "working", 1)
            engine.update(merged(a_working, b_working), settings())

            engine.update(merged(agent("p1", "term1", "done", 2), b_working), settings())
            clock.advance(2)
            engine.update(merged(agent("p1", "term1", "working", 3), b_working), settings())
            clock.advance(2)
            self.assertEqual(engine.tick(), [])

            a_done = agent("p1", "term1", "done", 4)
            engine.update(merged(a_done, b_working), settings())
            clock.advance(1)
            b_done = agent("p2", "term2", "done", 2)
            engine.update(merged(a_done, b_done), settings())
            clock.advance(2)
            effects = notices(engine.tick())
            self.assertEqual(len(effects), 1)
            self.assertEqual(set(effects[0]["entry_ids"]), {entry["id"] for entry in engine.view()["entries"]})
            self.assertEqual(effects[0]["status"], "done")
            self.assertEqual([action["id"] for action in effects[0]["actions"]], ["open"])

    def test_mixed_group_prefers_blocked_and_keeps_meaningful_context(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            clock.set_local(21, 59)
            configured = settings(quietStart="22:00", quietEnd="07:00")
            engine = ATTENTION.AttentionEngine(directory, clock)

            def with_context(state):
                server = state["servers"][0]
                server.update({"host": "build-host", "session": "federation"})
                server["snapshot"]["workspaces"][0]["label"] = "Release train"
                for tab in server["snapshot"]["tabs"]:
                    tab["label"] = "42" if tab["tab_id"] == "numeric" else "Verification"
                return state

            builder = agent("p1", "term1", "working", 1, tab="numeric", name="Builder")
            reviewer = agent("p2", "term2", "working", 1, tab="named", name="Reviewer")
            engine.update(with_context(merged(builder, reviewer)), configured)

            clock.set_local(22, 0)
            builder = agent("p1", "term1", "blocked", 2, tab="numeric", name="Builder")
            reviewer = agent("p2", "term2", "done", 2, tab="named", name="Reviewer")
            engine.update(with_context(merged(builder, reviewer)), configured)
            self.assertEqual(engine.tick(), [])

            clock.set_local(7, 0)
            effect = notices(engine.tick())[0]
            self.assertEqual(effect["status"], "blocked")
            self.assertEqual([action["id"] for action in effect["actions"]], ["open"])
            self.assertIn("Release train", effect["summary"])
            self.assertIn("Builder", effect["body"])
            self.assertIn("Reviewer", effect["body"])
            self.assertIn("Verification", effect["body"])
            self.assertIn("build-host", effect["body"])
            self.assertIn("federation", effect["body"])
            self.assertNotIn("42", effect["body"])
            self.assertEqual(engine.command("open", effect["entry_ids"][0])["action"], "go")

    def test_snooze_expiry_realerts_once_at_the_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            engine = ATTENTION.AttentionEngine(directory, clock)
            engine.update(merged(agent("p1", "term1", "working", 1)), settings())
            blocked = agent("p1", "term1", "blocked", 2)
            engine.update(merged(blocked), settings(snoozeMinutes=10))
            clock.advance(1)
            first = notices(engine.tick())[0]

            engine.command("snooze", first["key"])
            self.assertEqual([effect["op"] for effect in engine.tick()], ["close"])
            clock.advance(599)
            self.assertEqual(engine.tick(), [])
            clock.advance(1)
            second = notices(engine.tick())
            self.assertEqual(len(second), 1)
            engine.notification_closed(second[0]["key"], 1)
            clock.advance(600)
            self.assertEqual(engine.tick(), [])

    def test_overnight_quiet_hours_include_start_and_exclude_end(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            clock.set_local(21, 59)
            configured = settings(quietStart="22:00", quietEnd="07:00")
            engine = ATTENTION.AttentionEngine(directory, clock)
            engine.update(merged(agent("p1", "term1", "working", 1)), configured)
            self.assertFalse(engine.view()["quiet"])

            clock.set_local(22, 0)
            engine.update(merged(agent("p1", "term1", "blocked", 2)), configured)
            self.assertTrue(engine.view()["quiet"])
            self.assertEqual(engine.tick(), [])
            clock.set_local(6, 59)
            self.assertEqual(engine.tick(), [])
            clock.set_local(7, 0)
            self.assertFalse(engine.view()["quiet"])
            self.assertEqual(len(notices(engine.tick())), 1)

    def test_workspace_mute_survives_replacement_and_remains_after_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            engine = ATTENTION.AttentionEngine(directory, clock)
            engine.update(merged(agent("p1", "term1", "working", 1)), settings())
            engine.update(merged(agent("p1", "term1", "blocked", 2)), settings())
            clock.advance(1)
            effect = notices(engine.tick())[0]
            engine.command("mute", effect["key"])
            project_id = engine.view()["entries"][0]["project_id"]

            replacement = ATTENTION.AttentionEngine(directory, clock)
            replacement.update(merged(agent("p1", "term1", "blocked", 2)), settings())
            self.assertTrue(replacement.view()["entries"][0]["muted"])
            self.assertEqual(replacement.tick(), [])

            replacement.update(merged(agent("p1", "term1", "working", 3)), settings())
            self.assertEqual([item["id"] for item in replacement.view()["muted_workspaces"]], [project_id])
            replacement.command("unmute", project_id)
            self.assertEqual(replacement.view()["muted_workspaces"], [])

    def test_connection_loss_notifies_once_after_grace_and_closes_on_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            engine = ATTENTION.AttentionEngine(directory, clock)
            working = agent("p1", "term1", "working", 1)
            engine.update(merged(working), settings())
            engine.update(merged(working, connected=False), settings())
            self.assertEqual(engine.view()["counts"]["offline"], 1)
            clock.advance(7.99)
            self.assertEqual(engine.tick(), [])
            clock.advance(0.01)
            effect = notices(engine.tick())[0]
            self.assertEqual(effect["status"], "offline")
            self.assertEqual([action["id"] for action in effect["actions"]], ["show"])
            engine.notification_result(effect["key"], effect["token"], True)
            engine.update(merged(working, connected=False), settings())
            self.assertEqual(engine.tick(), [])

            engine.update(merged(working), settings())
            self.assertEqual([item["op"] for item in engine.tick()], ["close"])
            self.assertEqual(engine.view()["counts"]["offline"], 0)
            engine.update(merged(working, connected=False), settings())
            clock.advance(8)
            self.assertEqual(len(notices(engine.tick())), 1)


    def test_enabling_alert_types_does_not_replay_existing_attention(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            engine = ATTENTION.AttentionEngine(directory, clock)
            blocked = agent("p1", "term1", "blocked", 1)
            engine.update(merged(blocked), settings(notifications=False))
            self.assertEqual(engine.view()["counts"]["blocked"], 1)
            self.assertEqual(engine.tick(), [])
            engine.update(merged(blocked), settings())
            self.assertEqual(engine.tick(), [])

            engine.update(merged(agent("p1", "term1", "working", 2)), settings(notifyDone=False))
            done = agent("p1", "term1", "done", 3)
            engine.update(merged(done), settings(notifyDone=False))
            self.assertEqual(engine.view()["counts"]["done"], 1)
            clock.advance(3)
            self.assertEqual(engine.tick(), [])
            engine.update(merged(done), settings(notifyDone=True))
            self.assertEqual(engine.tick(), [])

class SafeStateFileTests(unittest.TestCase):
    def test_round_trip_then_refuses_symlink_fifo_hardlink_and_oversize_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ATTENTION.SafeStateFile("state.json", directory, max_bytes=128)
            default = {"missing": True}
            self.assertEqual(state.load(default), default)
            state.save({"saved": [1, 2, 3]})
            self.assertEqual(state.load(None), {"saved": [1, 2, 3]})

            victim = Path(directory) / "victim"
            victim.write_text("must survive")
            os.chmod(victim, 0o600)
            target = Path(directory) / "unsafe.json"
            target.symlink_to(victim)
            unsafe = ATTENTION.SafeStateFile("unsafe.json", directory)
            with self.assertRaises(ATTENTION.SafeStateError):
                unsafe.load(None)
            with self.assertRaises(ATTENTION.SafeStateError):
                unsafe.save({"replace": True})
            self.assertEqual(victim.read_text(), "must survive")

            fifo_path = Path(directory) / "fifo.json"
            os.mkfifo(fifo_path, 0o600)
            with self.assertRaises(ATTENTION.SafeStateError):
                ATTENTION.SafeStateFile("fifo.json", directory).load(None)

            first_link = Path(directory) / "linked-source"
            first_link.write_text("{}")
            os.chmod(first_link, 0o600)
            os.link(first_link, Path(directory) / "linked.json")
            with self.assertRaises(ATTENTION.SafeStateError):
                ATTENTION.SafeStateFile("linked.json", directory).load(None)

            oversized = Path(directory) / "large.json"
            oversized.write_bytes(b" " * 129)
            os.chmod(oversized, 0o600)
            with self.assertRaises(ATTENTION.SafeStateError):
                ATTENTION.SafeStateFile("large.json", directory, max_bytes=128).load(None)

            attention_path = Path(directory) / "attention.json"
            attention_path.symlink_to(victim)
            engine = ATTENTION.AttentionEngine(directory, Clock())
            engine.update({"servers": []}, settings())
            self.assertTrue(engine.view()["error"])
            self.assertEqual(victim.read_text(), "must survive")


if __name__ == "__main__":
    unittest.main()
