#!/usr/bin/env python3
"""Persistent, stdlib-only attention and notification policy for Omaherdr."""

import copy
import hashlib
import json
import os
import re
import secrets
import stat
import time
import unicodedata


_STATE_VERSION = 1
_MAX_ENTRIES = 256
_MAX_LEDGER = 512
_MAX_ENDPOINTS = 64
_MAX_MUTED = 128
_MAX_SLOTS = 512
_MAX_TEXT = 160
_BLOCK_SETTLE_SEC = 0.75
_CONNECTION_GRACE_SEC = 8.0
_NOTIFICATION_RETRY_LIMIT = 3
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_QUIET_TIME = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])\Z")


class SafeStateError(RuntimeError):
    """A state path or payload could not be used without weakening safety."""


class SafeStateFile:
    """JSON state stored relative to one held, verified private directory."""

    def __init__(self, name, state_dir=None, max_bytes=1048576):
        if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name) or name in (".", ".."):
            raise ValueError("state name must be a fixed safe basename")
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        self.name = name
        self.max_bytes = max_bytes
        self.path = self._state_path(state_dir)
        self._dirfd = None
        self._dirfd = self._open_private_directory(self.path)

    @staticmethod
    def _state_path(state_dir):
        if state_dir is None:
            base = os.environ.get("XDG_STATE_HOME")
            if not base:
                home = os.environ.get("HOME") or os.path.expanduser("~")
                base = os.path.join(home, ".local", "state")
            state_dir = os.path.join(base, "omarchy", "omaherdr")
        path = os.path.abspath(os.path.expanduser(os.fspath(state_dir)))
        if "\0" in path:
            raise ValueError("state directory contains a null byte")
        return path

    @staticmethod
    def _open_private_directory(path):
        parts = [part for part in path.split(os.sep) if part]
        if any(part in (".", "..") for part in parts):
            raise SafeStateError("refusing unsafe state directory")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(os.sep if os.path.isabs(path) else ".", flags)
        try:
            if not parts:
                raise SafeStateError("refusing filesystem root as state directory")
            for part in parts:
                try:
                    nextfd = os.open(part, flags | nofollow, dir_fd=fd)
                except FileNotFoundError:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                    nextfd = os.open(part, flags | nofollow, dir_fd=fd)
                except OSError as error:
                    raise SafeStateError("refusing unsafe state directory") from error
                os.close(fd)
                fd = nextfd
                info = os.fstat(fd)
                if not stat.S_ISDIR(info.st_mode):
                    raise SafeStateError("state path contains a non-directory")
            info = os.fstat(fd)
            if info.st_uid != os.geteuid():
                raise SafeStateError("state directory is not owned by the current user")
            if stat.S_IMODE(info.st_mode) != 0o700:
                try:
                    os.fchmod(fd, 0o700)
                except OSError as error:
                    raise SafeStateError("state directory is not private") from error
            return fd
        except BaseException:
            os.close(fd)
            raise

    def close(self):
        if self._dirfd is not None:
            os.close(self._dirfd)
            self._dirfd = None

    def __del__(self):
        try:
            self.close()
        except OSError:
            pass

    def _open_existing(self):
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(self.name, flags, dir_fd=self._dirfd)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise SafeStateError("refusing unsafe state file") from error
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise SafeStateError("state file is not regular")
            if info.st_uid != os.geteuid() or info.st_nlink != 1:
                raise SafeStateError("state file has unsafe ownership or links")
            if stat.S_IMODE(info.st_mode) & 0o077:
                raise SafeStateError("state file is not private")
            if info.st_size > self.max_bytes:
                raise SafeStateError("state file exceeds its size limit")
            try:
                os.set_blocking(fd, True)
            except (AttributeError, OSError):
                pass
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _read_bytes(self):
        fd = self._open_existing()
        if fd is None:
            return None
        try:
            chunks = []
            total = 0
            while total <= self.max_bytes:
                chunk = os.read(fd, min(65536, self.max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > self.max_bytes:
                raise SafeStateError("state file grew past its size limit")
            return b"".join(chunks)
        finally:
            os.close(fd)

    def load(self, default):
        raw = self._read_bytes()
        if raw is None:
            return copy.deepcopy(default)
        try:
            return json.loads(raw.decode("utf-8", "strict"))
        except (UnicodeError, ValueError, RecursionError) as error:
            raise SafeStateError("state file is not valid JSON") from error

    def _encode(self, value):
        pieces = []
        total = 0
        try:
            iterator = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False).iterencode(value)
            for piece in iterator:
                encoded = piece.encode("utf-8")
                total += len(encoded)
                if total > self.max_bytes:
                    raise SafeStateError("state payload exceeds its size limit")
                pieces.append(encoded)
        except (TypeError, ValueError, RecursionError) as error:
            raise SafeStateError("state payload is not valid JSON") from error
        return b"".join(pieces)

    def save(self, value):
        payload = self._encode(value)
        existing = self._open_existing()
        if existing is not None:
            os.close(existing)
        temporary = ".%s.%s.tmp" % (self.name, secrets.token_hex(12))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(temporary, flags, 0o600, dir_fd=self._dirfd)
        except OSError as error:
            raise SafeStateError("could not create state temporary") from error
        published = False
        try:
            os.fchmod(fd, 0o600)
            remaining = memoryview(payload)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise SafeStateError("short state write")
                remaining = remaining[written:]
            os.fsync(fd)
            os.rename(temporary, self.name, src_dir_fd=self._dirfd, dst_dir_fd=self._dirfd)
            published = True
            os.fsync(self._dirfd)
        except OSError as error:
            raise SafeStateError("could not publish state file") from error
        finally:
            os.close(fd)
            if not published:
                try:
                    os.unlink(temporary, dir_fd=self._dirfd)
                except OSError:
                    pass


def _hash_id(prefix, *parts):
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", "replace"))
        digest.update(b"\0")
    return prefix + digest.hexdigest()[:24]


def _plain(value, limit=_MAX_TEXT):
    if value is None:
        return ""
    text = str(value)
    cleaned = []
    for char in text:
        if char in "<>&" or unicodedata.category(char).startswith("C"):
            cleaned.append(" ")
        else:
            cleaned.append(char)
    return " ".join("".join(cleaned).split())[:limit]


def _opaque(value, limit=256):
    if value is None:
        return ""
    return "".join(char for char in str(value) if not unicodedata.category(char).startswith("C"))[:limit]


def _number(value, default=0.0):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = float(value)
        if value == value and value not in (float("inf"), float("-inf")):
            return value
    return float(default)


def _sequence(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, str)):
        return _opaque(value, 96)
    return None


def _marker(status, sequence):
    return "%s:%s" % (status, sequence if sequence is not None else "legacy")


def _quiet_minute(value):
    match = _QUIET_TIME.fullmatch(value) if isinstance(value, str) else None
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


class AttentionEngine:
    """State transition policy. It neither imports Gio nor executes Herdr."""

    DEFAULT_SETTINGS = {
        "notifications": False,
        "notifyDone": True,
        "completionDelaySec": 3,
        "snoozeMinutes": 10,
        "quietStart": "",
        "quietEnd": "",
        "watchSavedMachines": False,
    }

    def __init__(self, state_dir=None, clock=time.time):
        self.clock = clock
        self.settings = dict(self.DEFAULT_SETTINGS)
        self._records = {}
        self._ledger = {}
        self._muted = {}
        self._endpoints = {}
        self._slots = {}
        self._active_by_base = {}
        self._pending_closes = set()
        self._failed_bases = set()
        self._token = 0
        self._runtime_updated = False
        self._server_usable = {}
        self._last_quiet = False
        self._dirty = False
        self._state_error = ""
        self._input_error = ""
        self._update_agent_budget = _MAX_ENTRIES
        self._state_file = None
        try:
            self._state_file = SafeStateFile("attention.json", state_dir=state_dir)
            stored = self._state_file.load(None)
            if stored is not None:
                self._restore(stored)
        except (OSError, SafeStateError, TypeError, ValueError) as error:
            self._state_error = _plain("Attention state unavailable: " + str(error), 240)
            if self._state_file is not None:
                self._state_file.close()
            self._state_file = None

    def _restore(self, stored):
        if not isinstance(stored, dict) or stored.get("version") != _STATE_VERSION:
            raise SafeStateError("unsupported attention state")
        self._token = max(0, min(int(stored.get("token", 0)), 2 ** 53))
        for record in stored.get("records", [])[:_MAX_ENTRIES]:
            clean = self._restore_record(record)
            self._records[clean["id"]] = clean
        ledger = stored.get("ledger", {})
        if not isinstance(ledger, dict):
            raise SafeStateError("invalid attention ledger")
        for identity, marker in list(ledger.items())[:_MAX_LEDGER]:
            identity = _opaque(identity, 64)
            marker = _opaque(marker, 128)
            if identity and marker:
                self._ledger[identity] = marker
        for muted in stored.get("muted", [])[:_MAX_MUTED]:
            if not isinstance(muted, dict):
                raise SafeStateError("invalid muted workspace")
            project_id = _opaque(muted.get("id"), 64)
            if project_id:
                self._muted[project_id] = {
                    "id": project_id,
                    "label": _plain(muted.get("label") or "Workspace"),
                    "server": _opaque(muted.get("server")),
                    "workspace_id": _opaque(muted.get("workspace_id")),
                }
        for endpoint in stored.get("endpoints", [])[:_MAX_ENDPOINTS]:
            clean = self._restore_endpoint(endpoint)
            self._endpoints[clean["id"]] = clean
        failed = stored.get("failed_bases", [])
        if not isinstance(failed, list):
            raise SafeStateError("invalid notification failure state")
        self._failed_bases = {_opaque(item, 96) for item in failed[:_MAX_SLOTS] if _opaque(item, 96)}

    def _restore_record(self, record):
        if not isinstance(record, dict):
            raise SafeStateError("invalid attention entry")
        required = ("id", "identity", "episode", "server", "status")
        if not all(record.get(key) for key in required):
            raise SafeStateError("invalid attention entry")
        status = _opaque(record.get("status"), 16)
        if status not in ("blocked", "done"):
            raise SafeStateError("invalid attention status")
        return {
            "id": _opaque(record["id"], 64),
            "identity": _opaque(record["identity"], 64),
            "episode": _opaque(record["episode"], 128),
            "state_change_seq": _sequence(record.get("state_change_seq")),
            "project_id": _opaque(record.get("project_id"), 64),
            "server": _opaque(record.get("server")),
            "host": _plain(record.get("host")),
            "session": _plain(record.get("session")),
            "workspace_id": _opaque(record.get("workspace_id")),
            "workspace": _plain(record.get("workspace") or "Workspace"),
            "tab": _plain(record.get("tab")),
            "agent": _plain(record.get("agent") or "Agent"),
            "pane_id": _opaque(record.get("pane_id")),
            "terminal_id": _opaque(record.get("terminal_id")),
            "status": status,
            "observed_since": _number(record.get("observed_since")),
            "connected": False,
            "can_open": False,
            "snoozed_until": max(0.0, _number(record.get("snoozed_until"))),
            "snooze_realerted": bool(record.get("snooze_realerted")),
            "alerted": bool(record.get("alerted", True)),
            "eligible_at": max(0.0, _number(record.get("eligible_at"))),
            "notify_failures": max(0, min(int(record.get("notify_failures", 0)), _NOTIFICATION_RETRY_LIMIT)),
            "release_group": bool(record.get("release_group")),
        }

    def _restore_endpoint(self, endpoint):
        if not isinstance(endpoint, dict) or not endpoint.get("id") or not endpoint.get("server"):
            raise SafeStateError("invalid endpoint state")
        return {
            "id": _opaque(endpoint.get("id"), 64),
            "server": _opaque(endpoint.get("server")),
            "host": _plain(endpoint.get("host")),
            "session": _plain(endpoint.get("session")),
            "present": False,
            "connected": False,
            "offline_since": max(0.0, _number(endpoint.get("offline_since"))),
            "alerted": True,  # Await this process's first connection baseline.
            "notify_failures": max(0, min(int(endpoint.get("notify_failures", 0)), _NOTIFICATION_RETRY_LIMIT)),
            "eligible_at": max(0.0, _number(endpoint.get("eligible_at"))),
        }

    def _persist_value(self):
        records = []
        for record in list(self._records.values())[:_MAX_ENTRIES]:
            records.append({key: record[key] for key in (
                "id", "identity", "episode", "state_change_seq", "project_id", "server", "host", "session",
                "workspace_id", "workspace", "tab", "agent", "pane_id", "terminal_id", "status", "observed_since",
                "snoozed_until", "snooze_realerted", "alerted", "eligible_at", "notify_failures", "release_group",
            )})
        endpoints = []
        for endpoint in list(self._endpoints.values())[:_MAX_ENDPOINTS]:
            endpoints.append({key: endpoint[key] for key in (
                "id", "server", "host", "session", "offline_since", "alerted", "notify_failures", "eligible_at",
            )})
        return {
            "version": _STATE_VERSION,
            "token": self._token,
            "records": records,
            "ledger": dict(list(self._ledger.items())[-_MAX_LEDGER:]),
            "muted": list(self._muted.values())[:_MAX_MUTED],
            "endpoints": endpoints,
            "failed_bases": sorted(self._failed_bases)[:_MAX_SLOTS],
        }

    def _persist(self):
        if not self._dirty or self._state_file is None:
            return
        try:
            self._state_file.save(self._persist_value())
            self._dirty = False
        except (OSError, SafeStateError, ValueError) as error:
            self._state_error = _plain("Attention state unavailable: " + str(error), 240)
            self._state_file.close()
            self._state_file = None

    def _normalise_settings(self, settings):
        source = settings if isinstance(settings, dict) else {}
        result = dict(self.DEFAULT_SETTINGS)
        for key in ("notifications", "notifyDone", "watchSavedMachines"):
            if isinstance(source.get(key), bool):
                result[key] = source[key]
        for key, low, high in (("completionDelaySec", 1, 30), ("snoozeMinutes", 1, 120)):
            value = source.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                result[key] = max(low, min(high, value))
        for key in ("quietStart", "quietEnd"):
            value = source.get(key)
            result[key] = value if isinstance(value, str) and _QUIET_TIME.fullmatch(value) else ""
        return result

    def _is_quiet(self, now=None, settings=None):
        settings = settings or self.settings
        start = _quiet_minute(settings.get("quietStart"))
        end = _quiet_minute(settings.get("quietEnd"))
        if start is None or end is None or start == end:
            return False
        local = time.localtime(self.clock() if now is None else now)
        minute = local.tm_hour * 60 + local.tm_min
        if start < end:
            return start <= minute < end
        return minute >= start or minute < end

    def _server_rows(self, state):
        servers = state.get("servers", []) if isinstance(state, dict) else []
        return servers[:_MAX_ENDPOINTS] if isinstance(servers, list) else []

    def _remove_record(self, entry_id, marker="gone"):
        record = self._records.pop(entry_id, None)
        if record is None:
            return
        self._ledger[record["identity"]] = marker
        self._close_slots_for_entry(entry_id)
        self._failed_bases.discard("entry:" + entry_id)
        self._dirty = True

    def _close_slot(self, key):
        slot = self._slots.get(key)
        if slot is None or slot.get("state") not in ("inflight", "open"):
            return
        slot["state"] = "closed"
        self._pending_closes.add(key)
        if self._active_by_base.get(slot["base"]) == key:
            self._active_by_base.pop(slot["base"], None)

    def _close_base(self, base):
        key = self._active_by_base.get(base)
        if key:
            self._close_slot(key)

    def _close_slots_for_entry(self, entry_id):
        for key, slot in list(self._slots.items()):
            if entry_id in slot.get("episodes", {}):
                self._close_slot(key)

    def _trim_ledger(self):
        current = {record["identity"] for record in self._records.values()}
        while len(self._ledger) > _MAX_LEDGER:
            candidate = next((identity for identity in self._ledger if identity not in current), next(iter(self._ledger)))
            self._ledger.pop(candidate, None)

    def _mark_failed(self, base):
        self._failed_bases.add(base)
        while len(self._failed_bases) > _MAX_SLOTS:
            self._failed_bases.discard(sorted(self._failed_bases)[0])

    def _trim_failures(self):
        keep = set()
        for base in self._failed_bases:
            if base.startswith("connection:"):
                endpoint = self._endpoints.get(base.split(":", 1)[1])
                if endpoint is not None and endpoint["present"] and not endpoint["connected"]:
                    keep.add(base)
                continue
            for slot in self._slots.values():
                if slot.get("base") != base:
                    continue
                if any(self._records.get(entry_id, {}).get("episode") == episode
                       for entry_id, episode in slot.get("episodes", {}).items()):
                    keep.add(base)
                    break
        if keep != self._failed_bases:
            self._failed_bases = keep
            self._dirty = True


    def _make_record_room(self):
        if len(self._records) < _MAX_ENTRIES:
            return True
        stale = [record for record in self._records.values() if not record["connected"]]
        if not stale:
            return False
        oldest = min(stale, key=lambda record: (record["observed_since"], record["id"]))
        self._remove_record(oldest["id"], oldest["episode"])
        return True


    def _prune(self):
        if len(self._slots) <= _MAX_SLOTS:
            return
        removable = [key for key, slot in self._slots.items() if slot.get("state") not in ("inflight", "open")]
        for key in removable[:len(self._slots) - _MAX_SLOTS]:
            self._slots.pop(key, None)

    def _workspace_maps(self, snapshot):
        workspaces = snapshot.get("workspaces", []) if isinstance(snapshot, dict) else []
        tabs = snapshot.get("tabs", []) if isinstance(snapshot, dict) else []
        workspace_map = {}
        tab_map = {}
        for workspace in workspaces[:_MAX_ENTRIES] if isinstance(workspaces, list) else []:
            if isinstance(workspace, dict):
                key = _opaque(workspace.get("workspace_id"))
                if key:
                    workspace_map[key] = workspace
        for tab in tabs[:_MAX_ENTRIES] if isinstance(tabs, list) else []:
            if isinstance(tab, dict):
                key = _opaque(tab.get("tab_id"))
                if key:
                    tab_map[key] = tab
        return workspace_map, tab_map

    def _agents(self, snapshot):
        if not isinstance(snapshot, dict):
            return []
        panes = snapshot.get("panes", [])
        agents = snapshot.get("agents", [])
        if ((isinstance(panes, list) and len(panes) > _MAX_ENTRIES)
                or (isinstance(agents, list) and len(agents) > _MAX_ENTRIES)):
            self._input_error = "Attention input exceeded its agent limit; some agents may not be monitored"
        pane_map = {}
        for pane in panes[:_MAX_ENTRIES] if isinstance(panes, list) else []:
            if isinstance(pane, dict):
                pane_id = _opaque(pane.get("pane_id"))
                if pane_id:
                    pane_map[pane_id] = pane
        rows = []
        used = set()
        for agent in agents[:_MAX_ENTRIES] if isinstance(agents, list) else []:
            if not isinstance(agent, dict):
                continue
            pane_id = _opaque(agent.get("pane_id"))
            merged = dict(pane_map.get(pane_id, {}))
            merged.update(agent)
            rows.append(merged)
            if pane_id:
                used.add(pane_id)
        for pane_id, pane in pane_map.items():
            if pane_id not in used and pane.get("agent"):
                rows.append(dict(pane))
        if len(rows) > _MAX_ENTRIES:
            self._input_error = "Attention input exceeded its agent limit; some agents may not be monitored"
        return rows[:_MAX_ENTRIES]

    def _entry_from_row(self, server, row, workspace_map, tab_map, since, now):
        identity_fields = row.get("identity") if isinstance(row.get("identity"), dict) else {}
        state_fields = row.get("state") if isinstance(row.get("state"), dict) else {}
        server_key = _opaque(server.get("key"))
        session = _plain(server.get("session"))
        pane_id = _opaque(row.get("pane_id") or identity_fields.get("pane_id"))
        terminal_id = _opaque(row.get("terminal_id") or identity_fields.get("terminal_id"))
        if not server_key or not pane_id:
            return None
        identity = _hash_id("i:", server_key, session,
                            "terminal:" + terminal_id if terminal_id else "legacy:" + pane_id)
        entry_id = _hash_id("e:", identity)
        workspace_id = _opaque(row.get("workspace_id") or identity_fields.get("workspace_id"))
        tab_id = _opaque(row.get("tab_id") or identity_fields.get("tab_id"))
        workspace = workspace_map.get(workspace_id, {})
        tab = tab_map.get(tab_id, {})
        project_id = _hash_id("p:", server_key, workspace_id or "server")
        state_value = row.get("state") if isinstance(row.get("state"), str) else ""
        status = _opaque(row.get("agent_status") or row.get("status") or state_value
                         or state_fields.get("agent_status") or state_fields.get("status"), 16)
        sequence = _sequence(row.get("state_change_seq") if row.get("state_change_seq") is not None
                             else state_fields.get("state_change_seq"))
        observed = _number(since.get(pane_id), now) if isinstance(since, dict) else now
        connected = server.get("connected") is True and server.get("ok") is True
        can_open = connected and server.get("can_open") is True and bool(pane_id) and bool(terminal_id)
        return {
            "id": entry_id,
            "identity": identity,
            "episode": _marker(status, sequence),
            "state_change_seq": sequence,
            "project_id": project_id,
            "server": server_key,
            "host": _plain(server.get("host") or server.get("label")),
            "session": session,
            "workspace_id": workspace_id,
            "workspace": _plain(workspace.get("label") or workspace.get("name") or "Workspace"),
            "tab": _plain(tab.get("label") or tab.get("name") or row.get("tab")),
            "agent": _plain(row.get("name") or row.get("agent") or "Agent"),
            "pane_id": pane_id,
            "terminal_id": terminal_id,
            "status": status,
            "observed_since": observed,
            "connected": connected,
            "can_open": can_open,
        }

    def _apply_server(self, server, now, baseline, enabled):
        if not isinstance(server, dict):
            return
        server_key = _opaque(server.get("key"))
        if not server_key:
            return
        connected = server.get("connected") is True and server.get("ok") is True
        endpoint_id = _hash_id("c:", server_key)
        endpoint = self._endpoints.get(endpoint_id)
        if endpoint is None and len(self._endpoints) >= _MAX_ENDPOINTS:
            stale_id = next((item_id for item_id, item in self._endpoints.items() if not item["present"]), None)
            if stale_id is None:
                self._input_error = "Attention input exceeded its endpoint limit; some agents may not be monitored"
                return
            self._close_base("connection:" + stale_id)
            self._endpoints.pop(stale_id, None)
        if endpoint is None:
            endpoint = {
                "id": endpoint_id,
                "server": server_key,
                "host": _plain(server.get("host") or server.get("label")),
                "session": _plain(server.get("session")),
                "present": True,
                "connected": connected,
                "offline_since": 0.0 if connected else now,
                "alerted": True,
                "notify_failures": 0,
                "eligible_at": now,
            }
            self._endpoints[endpoint_id] = endpoint
            self._dirty = True
        else:
            was_connected = endpoint["connected"]
            endpoint.update({
                "host": _plain(server.get("host") or server.get("label")),
                "session": _plain(server.get("session")),
                "present": True,
                "connected": connected,
            })
            if connected and not was_connected:
                endpoint["offline_since"] = 0.0
                endpoint["alerted"] = False
                endpoint["notify_failures"] = 0
                self._close_base("connection:" + endpoint_id)
                self._failed_bases.discard("connection:" + endpoint_id)
                self._dirty = True
            elif not connected and was_connected:
                endpoint["offline_since"] = now
                endpoint["eligible_at"] = now + _CONNECTION_GRACE_SEC
                endpoint["alerted"] = not enabled
                endpoint["notify_failures"] = 0
                self._dirty = True
            elif not connected and not endpoint["offline_since"]:
                endpoint["offline_since"] = now
                self._dirty = True

        snapshot = server.get("snapshot")
        workspace_map, tab_map = self._workspace_maps(snapshot)
        since = server.get("since") if isinstance(server.get("since"), dict) else {}
        seen = set()
        if isinstance(snapshot, dict):
            for row in self._agents(snapshot):
                if self._update_agent_budget <= 0:
                    self._input_error = "Attention input exceeded its agent limit; some agents may not be monitored"
                    break
                self._update_agent_budget -= 1
                entry = self._entry_from_row(server, row, workspace_map, tab_map, since, now)
                if entry is None:
                    continue
                muted = self._muted.get(entry["project_id"])
                if muted is not None:
                    muted.update({"label": entry["workspace"], "server": entry["server"],
                                  "workspace_id": entry["workspace_id"]})
                identity = entry["identity"]
                seen.add(identity)
                previous_marker = self._ledger.get(identity)
                current = self._records.get(entry["id"])
                if entry["status"] not in ("blocked", "done"):
                    if current is not None:
                        self._remove_record(current["id"], entry["episode"])
                    self._ledger[identity] = entry["episode"]
                    continue
                if current is not None and current["episode"] == entry["episode"]:
                    for key in ("project_id", "server", "host", "session", "workspace_id", "workspace", "tab", "agent", "pane_id", "terminal_id", "connected", "can_open"):
                        current[key] = entry[key]
                    self._ledger[identity] = entry["episode"]
                    if not entry["connected"]:
                        self._close_slots_for_entry(current["id"])
                    continue
                if current is not None:
                    self._remove_record(current["id"], entry["episode"])
                if not self._make_record_room():
                    self._input_error = "Attention state reached its entry limit; some agents may not be monitored"
                    self._ledger[identity] = entry["episode"]
                    continue
                suppress = (not enabled or (entry["status"] == "done" and not self.settings["notifyDone"])
                            or previous_marker == entry["episode"] or (baseline and previous_marker is None)
                            or (not connected and previous_marker is None))
                delay = _BLOCK_SETTLE_SEC if entry["status"] == "blocked" else self.settings["completionDelaySec"]
                entry.update({
                    "snoozed_until": 0.0,
                    "snooze_realerted": False,
                    "alerted": suppress,
                    "eligible_at": now + delay,
                    "notify_failures": 0,
                    "release_group": False,
                })
                self._records[entry["id"]] = entry
                self._ledger[identity] = entry["episode"]
                self._dirty = True
            if connected and not self._input_error:
                for record in list(self._records.values()):
                    if record["server"] == server_key and record["identity"] not in seen:
                        self._remove_record(record["id"], "gone")
        if not connected:
            for record in self._records.values():
                if record["server"] == server_key:
                    if record["connected"]:
                        self._close_slots_for_entry(record["id"])
                    record["connected"] = False
                    record["can_open"] = False

    def update(self, state, settings):
        now = self.clock()
        old_settings = self.settings
        old_enabled = old_settings["notifications"]
        old_quiet = self._last_quiet if self._runtime_updated else self._is_quiet(now, old_settings)
        self.settings = self._normalise_settings(settings)
        enabled = self.settings["notifications"]
        first_update = not self._runtime_updated
        servers = state.get("servers", []) if isinstance(state, dict) else []
        over_server_limit = isinstance(servers, list) and len(servers) > _MAX_ENDPOINTS
        self._input_error = ("Attention input was truncated; some agents may not be monitored"
                             if isinstance(state, dict) and state.get("truncated") is True else
                             "Attention input exceeded its server limit; some agents may not be monitored"
                             if over_server_limit else "")
        self._update_agent_budget = _MAX_ENTRIES

        for endpoint in self._endpoints.values():
            endpoint["present"] = False
        current_servers = set()
        for server in self._server_rows(state):
            if not isinstance(server, dict):
                continue
            server_key = _opaque(server.get("key"))
            current_servers.add(server_key)
            usable = (server.get("ok") is True and server.get("connected") is True
                      and isinstance(server.get("snapshot"), dict))
            baseline = usable and not self._server_usable.get(server_key, False)
            self._apply_server(server, now, baseline, enabled)
            self._server_usable[server_key] = usable
        if not self._input_error:
            for server_key in list(self._server_usable):
                if server_key not in current_servers:
                    self._server_usable[server_key] = False
            for record in self._records.values():
                if record["server"] not in current_servers:
                    if record["connected"]:
                        self._close_slots_for_entry(record["id"])
                    record["connected"] = False
                    record["can_open"] = False
            for endpoint_id, endpoint in list(self._endpoints.items()):
                if not endpoint["present"]:
                    self._close_base("connection:" + endpoint["id"])
                    self._endpoints.pop(endpoint_id, None)

        else:
            for server_key in current_servers:
                self._server_usable[server_key] = False

        if old_enabled and not enabled:
            self._suppress_and_close()
        elif not old_enabled and enabled and not first_update:
            for record in self._records.values():
                record["alerted"] = True
                record["release_group"] = False
            for endpoint in self._endpoints.values():
                if not endpoint["connected"]:
                    endpoint["alerted"] = True
            self._dirty = True
        if old_settings["notifyDone"] and not self.settings["notifyDone"]:
            for record in self._records.values():
                if record["status"] == "done":
                    record["alerted"] = True
                    record["release_group"] = False
                    self._close_slots_for_entry(record["id"])
            self._dirty = True
        if not old_settings["notifyDone"] and self.settings["notifyDone"]:
            for record in self._records.values():
                if record["status"] == "done":
                    record["alerted"] = True
                    record["release_group"] = False
            self._dirty = True

        self._trim_ledger()
        self._trim_failures()

        new_quiet = self._is_quiet(now)
        if not old_quiet and new_quiet:
            self._close_active()
        elif old_quiet and not new_quiet:
            self._release_quiet(now)
        self._last_quiet = new_quiet
        self._runtime_updated = True
        self._dirty = True
        self._persist()

    def _close_active(self):
        for key, slot in list(self._slots.items()):
            if slot.get("state") in ("inflight", "open"):
                self._close_slot(key)

    def _suppress_and_close(self):
        self._close_active()
        for record in self._records.values():
            record["alerted"] = True
            record["release_group"] = False
        for endpoint in self._endpoints.values():
            if not endpoint["connected"]:
                endpoint["alerted"] = True
        self._dirty = True

    def _release_quiet(self, now):
        for record in self._records.values():
            if not record["alerted"]:
                record["eligible_at"] = now
                record["release_group"] = True
        for endpoint in self._endpoints.values():
            if not endpoint["connected"] and not endpoint["alerted"]:
                endpoint["eligible_at"] = max(endpoint["eligible_at"], endpoint["offline_since"] + _CONNECTION_GRACE_SEC)
        self._dirty = True

    def _eligible(self, record, now):
        return (record["connected"] and not record["alerted"] and not self._is_muted(record)
                and (record["status"] != "done" or self.settings["notifyDone"])
                and record["snoozed_until"] <= now and record["eligible_at"] <= now)

    def _is_muted(self, record):
        return record["project_id"] in self._muted

    def _start_notification(self, base, records, summary, body, actions):
        old_key = self._active_by_base.get(base)
        if old_key:
            self._close_slot(old_key)
        self._token += 1
        key = _hash_id("n:", base, self._token)
        episodes = {record["id"]: record["episode"] for record in records}
        self._slots[key] = {
            "base": base,
            "token": self._token,
            "episodes": episodes,
            "entry_ids": list(episodes),
            "state": "inflight",
        }
        self._active_by_base[base] = key
        for record in records:
            record["alerted"] = True
            record["release_group"] = False
        self._dirty = True
        self._prune()
        return {
            "op": "notify",
            "key": key,
            "token": self._token,
            "summary": summary,
            "body": body,
            "actions": actions,
            "entry_ids": list(episodes),
            "urgency": 1,
            "timeout_ms": 8000,
        }

    def _connection_effects(self, now):
        effects = []
        for endpoint in self._endpoints.values():
            if (endpoint["present"] and not endpoint["connected"] and not endpoint["alerted"]
                    and endpoint["eligible_at"] <= now and endpoint["offline_since"] + _CONNECTION_GRACE_SEC <= now):
                base = "connection:" + endpoint["id"]
                self._token += 1
                key = _hash_id("n:", base, self._token)
                old_key = self._active_by_base.get(base)
                if old_key:
                    self._close_slot(old_key)
                self._slots[key] = {
                    "base": base,
                    "token": self._token,
                    "episodes": {},
                    "entry_ids": [endpoint["id"]],
                    "state": "inflight",
                }
                self._active_by_base[base] = key
                endpoint["alerted"] = True
                self._dirty = True
                self._prune()
                effects.append({
                    "op": "notify",
                    "key": key,
                    "token": self._token,
                    "summary": "Connection unavailable — " + endpoint["host"],
                    "body": "Session " + endpoint["session"] + "; last known attention stays in the list.",
                    "actions": [{"id": "show", "label": "Show attention"}],
                    "entry_ids": [endpoint["id"]],
                    "urgency": 1,
                    "timeout_ms": 8000,
                })
        return effects

    def _notice_body(self, records):
        first = records[0]
        names = ", ".join(record["agent"] for record in records[:4])
        if len(records) > 4:
            names += " (+%d more)" % (len(records) - 4)
        location = first["host"] + " · " + first["session"]
        if len(records) == 1 and first["tab"]:
            location += " · tab " + first["tab"]
        return names + "\n" + location

    def _notice_actions(self, record=None):
        can_open = record is not None and record["connected"] and record["can_open"]
        return [{"id": "open" if can_open else "show", "label": "Open agent" if can_open else "Show attention"},
                {"id": "snooze", "label": "Snooze %dm" % self.settings["snoozeMinutes"]},
                {"id": "mute", "label": "Mute workspace"}]

    def tick(self):
        now = self.clock()
        quiet = self._is_quiet(now)
        if quiet != self._last_quiet:
            if quiet:
                self._close_active()
            else:
                self._release_quiet(now)
            self._last_quiet = quiet

        for record in self._records.values():
            if record["snoozed_until"] and record["snoozed_until"] <= now:
                record["snoozed_until"] = 0.0
                if not record["snooze_realerted"]:
                    record["snooze_realerted"] = True
                    record["alerted"] = False
                    record["eligible_at"] = now
                self._dirty = True

        effects = []
        if self.settings["notifications"] and not quiet:
            effects.extend(self._connection_effects(now))
            candidates = [record for record in self._records.values() if self._eligible(record, now)]
            consumed = set()

            release_groups = {}
            for record in candidates:
                if record["release_group"]:
                    release_groups.setdefault((record["server"], record["project_id"]), []).append(record)
            for records in release_groups.values():
                effects.append(self._start_notification(
                    _hash_id("group:", records[0]["server"], records[0]["project_id"]), records,
                    "Pending attention — " + records[0]["workspace"], self._notice_body(records), self._notice_actions(),
                ))
                consumed.update(record["id"] for record in records)

            done_groups = {}
            for record in self._records.values():
                if record["id"] in consumed or record["status"] != "done":
                    continue
                if (self.settings["notifyDone"] and record["connected"] and not record["alerted"]
                        and not self._is_muted(record) and record["snoozed_until"] <= now):
                    done_groups.setdefault((record["server"], record["project_id"]), []).append(record)
            for records in done_groups.values():
                if not any(record["eligible_at"] <= now for record in records):
                    continue
                if len(records) == 1:
                    record = records[0]
                    effects.append(self._start_notification(
                        "entry:" + record["id"], records, "Finished — " + record["workspace"],
                        self._notice_body(records), self._notice_actions(record),
                    ))
                else:
                    effects.append(self._start_notification(
                        _hash_id("group:", records[0]["server"], records[0]["project_id"]), records,
                        "%d agents finished — " % len(records) + records[0]["workspace"],
                        self._notice_body(records), self._notice_actions(),
                    ))
                consumed.update(record["id"] for record in records)

            for record in candidates:
                if record["id"] in consumed:
                    continue
                effects.append(self._start_notification(
                    "entry:" + record["id"], [record], "Needs input — " + record["workspace"],
                    self._notice_body([record]), self._notice_actions(record),
                ))

        close_effects = [{"op": "close", "key": key} for key in sorted(self._pending_closes)]
        self._pending_closes.clear()
        self._persist()
        return close_effects + effects

    def _records_for_target(self, target):
        target = _opaque(target, 96)
        record = self._records.get(target)
        if record is not None:
            return [record]
        slot = self._slots.get(target)
        if slot is not None:
            records = []
            for entry_id, episode in slot.get("episodes", {}).items():
                current = self._records.get(entry_id)
                if current is not None and current["episode"] == episode:
                    records.append(current)
            return records
        return [record for record in self._records.values() if record["project_id"] == target]

    def _filter_for_target(self, target, records=None):
        records = records if records is not None else self._records_for_target(target)
        if records:
            return records[0]["workspace"]
        muted = self._muted.get(_opaque(target, 96))
        if muted:
            return muted["label"]
        slot = self._slots.get(_opaque(target, 96))
        if slot:
            for entry_id in slot.get("entry_ids", []):
                endpoint = self._endpoints.get(entry_id)
                if endpoint:
                    return endpoint["host"] or endpoint["session"]
        return ""

    @staticmethod
    def _show_action(label=""):
        action = {"kind": "action", "action": "show_attention"}
        if label:
            action["filter"] = label
        return action

    def command(self, action, target=""):
        action = _opaque(action, 32)
        target = _opaque(target, 96)
        records = self._records_for_target(target)
        if action == "open":
            if len(records) == 1:
                record = records[0]
                if record["connected"] and record["can_open"] and record["pane_id"] and record["terminal_id"]:
                    return {
                        "kind": "action",
                        "action": "go",
                        "server": record["server"],
                        "kind_target": "pane",
                        "id": record["pane_id"],
                        "terminal_id": record["terminal_id"],
                    }
            return self._show_action(self._filter_for_target(target, records))
        if action == "show":
            return self._show_action(self._filter_for_target(target, records))
        if action == "snooze":
            until = self.clock() + self.settings["snoozeMinutes"] * 60
            for record in records:
                record["snoozed_until"] = until
                record["snooze_realerted"] = False
                record["alerted"] = True
                record["release_group"] = False
                self._close_slots_for_entry(record["id"])
            if records:
                self._dirty = True
                self._persist()
            return None
        if action == "unsnooze":
            now = self.clock()
            for record in records:
                record["snoozed_until"] = 0.0
                record["snooze_realerted"] = True
                record["alerted"] = False
                record["eligible_at"] = now
                record["release_group"] = len(records) > 1
            if records:
                self._dirty = True
                self._persist()
            return None
        if action == "mute":
            for record in records:
                if record["project_id"] not in self._muted and len(self._muted) >= _MAX_MUTED:
                    continue
                self._muted[record["project_id"]] = {
                    "id": record["project_id"],
                    "label": record["workspace"],
                    "server": record["server"],
                    "workspace_id": record["workspace_id"],
                }
                self._close_slots_for_entry(record["id"])
            if records:
                self._dirty = True
                self._persist()
            return None
        if action == "unmute":
            projects = {record["project_id"] for record in records}
            if target in self._muted:
                projects.add(target)
            now = self.clock()
            for project_id in projects:
                self._muted.pop(project_id, None)
                project_records = [record for record in self._records.values() if record["project_id"] == project_id]
                for record in project_records:
                    record["alerted"] = False
                    record["eligible_at"] = now
                    record["release_group"] = True
            if projects:
                self._dirty = True
                self._persist()
            return None
        if action == "clear_mutes":
            projects = set(self._muted)
            self._muted.clear()
            now = self.clock()
            for record in self._records.values():
                if record["project_id"] in projects:
                    record["alerted"] = False
                    record["eligible_at"] = now
                    record["release_group"] = True
            if projects:
                self._dirty = True
                self._persist()
            return None
        return None

    def notification_result(self, key, token, success):
        key = _opaque(key, 96)
        slot = self._slots.get(key)
        if (slot is None or slot.get("state") != "inflight" or not isinstance(token, int)
                or isinstance(token, bool) or token != slot.get("token")):
            return
        base = slot["base"]
        if success:
            slot["state"] = "open"
            self._failed_bases.discard(base)
            if base.startswith("connection:"):
                endpoint_id = slot.get("entry_ids", [""])[0]
                endpoint = self._endpoints.get(endpoint_id)
                if endpoint is not None:
                    endpoint["notify_failures"] = 0
            else:
                for entry_id, episode in slot.get("episodes", {}).items():
                    record = self._records.get(entry_id)
                    if record is not None and record["episode"] == episode:
                        record["notify_failures"] = 0
            successful_ids = set(slot.get("episodes", {}))
            if successful_ids:
                for prior in self._slots.values():
                    if successful_ids.intersection(prior.get("episodes", {})):
                        self._failed_bases.discard(prior.get("base"))
        else:
            slot["state"] = "failed"
            if self._active_by_base.get(base) == key:
                self._active_by_base.pop(base, None)
            self._mark_failed(base)
            now = self.clock()
            if base.startswith("connection:"):
                endpoint_id = slot.get("entry_ids", [""])[0]
                endpoint = self._endpoints.get(endpoint_id)
                if endpoint is not None and not endpoint["connected"]:
                    endpoint["notify_failures"] += 1
                    if endpoint["notify_failures"] <= _NOTIFICATION_RETRY_LIMIT:
                        endpoint["alerted"] = False
                        endpoint["eligible_at"] = now + min(300, 15 * (2 ** (endpoint["notify_failures"] - 1)))
            else:
                for entry_id, episode in slot.get("episodes", {}).items():
                    record = self._records.get(entry_id)
                    if record is not None and record["episode"] == episode:
                        record["notify_failures"] += 1
                        if record["notify_failures"] <= _NOTIFICATION_RETRY_LIMIT:
                            record["alerted"] = False
                            record["eligible_at"] = now + min(300, 15 * (2 ** (record["notify_failures"] - 1)))
        self._dirty = True
        self._persist()

    def notification_closed(self, key, reason):
        del reason
        key = _opaque(key, 96)
        slot = self._slots.get(key)
        if slot is None or slot.get("state") not in ("inflight", "open"):
            return
        slot["state"] = "closed"
        if self._active_by_base.get(slot["base"]) == key:
            self._active_by_base.pop(slot["base"], None)
        self._dirty = True
        self._persist()

    def close_all(self):
        keys = set(self._pending_closes)
        self._pending_closes.clear()
        for key, slot in self._slots.items():
            if slot.get("state") in ("inflight", "open"):
                keys.add(key)
                slot["state"] = "closed"
        self._active_by_base.clear()
        if keys:
            self._dirty = True
            self._persist()
        return [{"op": "close", "key": key} for key in sorted(keys)]

    def view(self):
        entries = []
        for record in self._records.values():
            entries.append({
                "id": record["id"],
                "project_id": record["project_id"],
                "server": record["server"],
                "host": record["host"],
                "session": record["session"],
                "workspace_id": record["workspace_id"],
                "workspace": record["workspace"],
                "tab": record["tab"],
                "agent": record["agent"],
                "pane_id": record["pane_id"],
                "terminal_id": record["terminal_id"],
                "status": record["status"],
                "observed_since": record["observed_since"],
                "connected": record["connected"],
                "can_open": record["can_open"],
                "snoozed_until": record["snoozed_until"],
                "muted": self._is_muted(record),
            })
        for endpoint in self._endpoints.values():
            if endpoint["present"] and not endpoint["connected"]:
                entries.append({
                    "kind": "connection",
                    "id": endpoint["id"],
                    "project_id": endpoint["id"],
                    "server": endpoint["server"],
                    "host": endpoint["host"],
                    "session": endpoint["session"],
                    "workspace_id": "",
                    "workspace": "",
                    "tab": "",
                    "agent": "Connection unavailable",
                    "pane_id": "",
                    "terminal_id": "",
                    "status": "offline",
                    "observed_since": endpoint["offline_since"],
                    "connected": False,
                    "can_open": False,
                    "snoozed_until": 0.0,
                    "muted": False,
                })
        order = {"blocked": 0, "done": 1, "offline": 2}
        entries.sort(key=lambda entry: (order.get(entry["status"], 9), entry["observed_since"], entry["id"]))
        entries = entries[:_MAX_ENTRIES]
        counts = {
            "blocked": sum(entry["status"] == "blocked" for entry in entries),
            "done": sum(entry["status"] == "done" for entry in entries),
            "offline": sum(entry["status"] == "offline" for entry in entries),
        }
        errors = []
        if self._state_error:
            errors.append(self._state_error)
        if self._input_error:
            errors.append(self._input_error)
        delivery_failed = (bool(self._failed_bases)
                           or any(record["notify_failures"] for record in self._records.values())
                           or any(endpoint["notify_failures"] for endpoint in self._endpoints.values()))
        if delivery_failed:
            errors.append("Desktop notification delivery failed; attention remains in the list")
        return {
            "available": True,
            "enabled": self.settings["notifications"],
            "quiet": self._is_quiet(),
            "error": _plain("; ".join(errors), 240),
            "entries": entries,
            "muted_workspaces": [
                {"id": item["id"], "label": item["label"], "server": item["server"]}
                for item in list(self._muted.values())[:_MAX_MUTED]
            ],
            "counts": counts,
        }
