import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Omaherdr: a bar icon and a TUI-style panel for every herdr you are attached
// to from this desktop. bin/omaherdr-daemon finds the sessions, follows their
// servers live and streams one JSON state per change; this file renders it and
// sends jump commands back. Keys: j/k move · Enter jump · v spaces/agents ·
// h redact · r cycle the bar text · i cycle the bar icon · R refresh · Esc close.

Panel {
  id: root
  moduleName: "njpatel.omaherdr"
  ipcTarget: "njpatel.omaherdr"
  manageIpc: false

  // ---------------------------------------------------------------- settings
  property string barMetric: String(setting("barMetric", "attention"))
  readonly property var barMetrics: ["attention", "all", "none"]
  function cycleBarMetric() {
    barMetric = barMetrics[(barMetrics.indexOf(barMetric) + 1) % barMetrics.length]
    Quickshell.execDetached(["omarchy", "bar", "set", "njpatel.omaherdr", "barMetric", barMetric])
  }

  readonly property var barIcons: ({
    "robot": "\u{f06a9}", "robot-happy": "\u{f1719}", "console": "\u{f018d}", "terminal": "\u{f0489}",
    "brain": "\u{f09d1}", "group": "\u{f000e}", "dashboard": "\u{f056e}", "flag": "\u{f023b}",
    "bell": "\u{f009a}", "server": "\u{f048b}", "lan": "\u{f0317}", "chip": "\u{f061a}", "pulse": "\u{f0430}"
  })
  property string barIconName: String(setting("barIcon", "robot"))
  readonly property string barIcon: barIcons[barIconName] !== undefined ? barIcons[barIconName] : barIconName
  function cycleBarIcon() {
    var names = Object.keys(barIcons)
    barIconName = names[(names.indexOf(barIconName) + 1) % names.length]
    Quickshell.execDetached(["omarchy", "bar", "set", "njpatel.omaherdr", "barIcon", barIconName])
  }

  property string viewMode: String(setting("view", "agents"))   // agents | spaces
  function toggleView() {
    viewMode = viewMode === "agents" ? "spaces" : "agents"
    Quickshell.execDetached(["omarchy", "bar", "set", "njpatel.omaherdr", "view", viewMode])
    cursor = 0
  }

  readonly property string daemonPath: Qt.resolvedUrl("bin/omaherdr-daemon").toString().replace(/^file:\/\//, "")

  function setting(name, fallback) {
    var s = root.settings || ({})
    return s[name] !== undefined && s[name] !== null ? s[name] : fallback
  }

  // ---------------------------------------------------------------- theme
  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color accent: Color.accent
  readonly property color dim: Qt.rgba(fg.r, fg.g, fg.b, 0.45)
  readonly property color faint: Qt.rgba(fg.r, fg.g, fg.b, 0.22)
  readonly property color hilite: Qt.rgba(fg.r, fg.g, fg.b, 0.10)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // Traffic lights: red / yellow / green from the theme's colors.toml (the
  // shell only exposes foreground/accent/urgent), muted grey for idle.
  // Bright variants first: the normal ones are often too muted to read at 6 px.
  property var palette: ({})
  readonly property color red: palette.bright_red || palette.red || urgent
  readonly property color yellow: palette.bright_yellow || palette.yellow || accent
  readonly property color green: palette.bright_green || palette.green || accent
  readonly property color grey: Color.muted || dim
  FileView {
    path: Quickshell.env("HOME") + "/.local/state/omarchy/current/theme/colors.toml"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.parsePalette(text())
  }
  function parsePalette(content) {
    var out = ({}), lines = String(content || "").split("\n")
    for (var i = 0; i < lines.length; i++) {
      var m = lines[i].match(/^\s*([a-z_]+)\s*=\s*"(#[0-9a-fA-F]{6,8})"/)
      if (m) out[m[1]] = m[2]
    }
    root.palette = out
  }

  // ---------------------------------------------------------------- state
  property var snap: null
  property bool scrub: false
  property int cursor: 0
  property string filter: ""       // substring, case-insensitive
  property bool filtering: false   // typing into the filter (/)
  function matches(text) {
    if (!filter) return true
    return String(text || "").toLowerCase().indexOf(filter.toLowerCase()) >= 0
  }
  function startFilter() {
    filtering = true
    Qt.callLater(function() { filterCatcher.forceActiveFocus() })
  }
  function stopFilter(clear) {
    if (clear) filter = ""
    filtering = false
    cursor = 0
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }
  property double nowMs: Date.now()
  readonly property var counts: snap && snap.counts ? snap.counts : ({})
  readonly property var servers: snap && snap.servers ? snap.servers : []
  readonly property bool alarming: (counts.blocked || 0) > 0
  readonly property bool attention: alarming || (counts.done || 0) > 0

  // Priority order and glyphs per agent status.
  readonly property var statusOrder: ({ "blocked": 0, "done": 1, "working": 2, "unknown": 3, "idle": 4 })
  function statusGlyph(st) {
    return st === "blocked" ? "!" : st === "done" ? "✓" : st === "working" ? "⠴" : st === "idle" ? "·" : st === "unknown" ? "?" : "—"
  }
  function statusColor(st) {
    return st === "blocked" ? red : st === "done" ? green : st === "working" ? yellow : st === "idle" ? grey : dim
  }
  function statusText(st) {
    return st === "blocked" ? "needs input" : st === "done" ? "done" : st === "working" ? "working" : st === "idle" ? "idle" : st === "unknown" ? "unknown" : ""
  }

  // ---------------------------------------------------------------- daemon
  Process {
    id: daemon
    command: [root.daemonPath]
    running: true
    stdinEnabled: true
    stdout: SplitParser {
      onRead: function(data) { root.parseState(data) }
    }
    stderr: SplitParser {
      onRead: function(data) { if (String(data).trim() !== "") console.warn("omaherdr", String(data).trim()) }
    }
    onExited: function(code, status) {
      console.warn("omaherdr", "daemon exited", code)
      restartTimer.start()
    }
  }
  Timer {
    id: restartTimer
    interval: 5000
    onTriggered: daemon.running = true
  }

  function parseState(text) {
    try {
      var parsed = JSON.parse(String(text || ""))
      if (parsed && typeof parsed === "object") { root.snap = parsed; root.nowMs = Date.now() }
    } catch (e) {
      console.warn("omaherdr", "bad state line", e)
    }
  }

  function send(cmd) {
    if (!daemon.running) { daemon.running = true; return }
    daemon.write(cmd + "\n")
  }
  function refresh() { send("refresh") }

  // Jump: focus the terminal window, then the space / tab / pane inside herdr.
  function go(target) {
    if (!target) return
    send("go " + target.server + " " + target.kind + " " + target.id)
    root.close()
  }

  Timer {
    interval: 30000
    running: root.opened
    repeat: true
    onTriggered: root.nowMs = Date.now()
  }

  onOpenedChanged: if (opened) {
    nowMs = Date.now()
    panelFlick.contentY = 0
    refresh()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  } else {
    filtering = false
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { root.refresh(); return "ok" }
    function scrub(): string { root.scrub = !root.scrub; return root.scrub ? "scrubbed" : "clear" }
    function view(): string { root.toggleView(); return root.viewMode }
    function filter(text: string): string { root.filter = text; return root.filter }
    function metric(name: string): string { root.barMetric = name; return root.barMetric }
    function metrics(): string {
      return JSON.stringify({ lines: root.rows.length, textImplicit: tuiText.implicitHeight, textHeight: tuiText.height,
                              flickContent: panelFlick.contentHeight, flickHeight: panelFlick.height, panelContent: panel.contentHeight })
    }
    function geometry(): string {
      return JSON.stringify({ x: panel.cardOrigin.x, y: panel.cardOrigin.y, w: panel.contentWidth, h: panel.contentHeight })
    }
  }

  // ---------------------------------------------------------------- formatting
  function fmtSince(ts) {
    if (!ts) return ""
    var s = Math.max(0, nowMs / 1000 - ts)
    if (s < 60) return "now"
    var d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60)
    if (d > 0) return d + "d" + h + "h"
    if (h > 0) return h + "h" + (m < 10 ? "0" : "") + m
    return m + "m"
  }
  function fmtAge(ts) {
    if (!ts) return "never"
    var s = nowMs / 1000 - ts
    return s < 90 ? "live" : fmtSince(ts) + " ago"
  }
  function noise(text) {
    var glyphs = "░▒▓█▓▒", h = 2166136261, out = ""
    for (var i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = (h * 16777619) >>> 0 }
    for (var j = 0; j < text.length; j++) {
      h ^= h << 13; h >>>= 0; h ^= h >>> 17; h ^= h << 5; h >>>= 0
      out += glyphs.charAt(h % glyphs.length)
    }
    return out
  }
  function label(text) { text = String(text || ""); return scrub ? noise(text) : text }

  // ---------------------------------------------------------------- layout
  readonly property int cols: 62
  readonly property int inner: cols - 4
  property var rows: []          // line index → jump target (or null)
  property var jumps: []         // ordered list of { line, target }
  property string tui: ""
  readonly property var scratch: ({ rows: [], jumps: [], line: 0 })

  function rebuild() { tui = buildTui() }
  onSnapChanged: rebuild()
  onCursorChanged: rebuild()
  onScrubChanged: rebuild()
  onFilterChanged: { cursor = 0; rebuild() }
  onFilteringChanged: rebuild()
  onViewModeChanged: rebuild()
  onNowMsChanged: rebuild()
  onBarMetricChanged: rebuild()
  onBarIconNameChanged: rebuild()
  onFgChanged: rebuild()
  Component.onCompleted: rebuild()

  function pad(s, w) { s = String(s); while (s.length < w) s += " "; return s }
  function lpad(s, w) { s = String(s); while (s.length < w) s = " " + s; return s }
  function rep(ch, n) { var s = ""; for (var i = 0; i < n; i++) s += ch; return s }
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/ /g, "&nbsp;")
  }
  function span(text, color, bg) {
    return "<span style=\"color:" + String(color) + (bg ? ";background-color:" + String(bg) : "") + "\">" + esc(text) + "</span>"
  }
  function frag(html, len) { return { html: html, len: len } }
  function cat() {
    var html = "", len = 0
    for (var i = 0; i < arguments.length; i++) { html += arguments[i].html; len += arguments[i].len }
    return frag(html, len)
  }
  function cell(text, w, color, right, bg) {
    text = String(text === null || text === undefined ? "" : text)
    if (text.length > w) text = w > 1 ? text.slice(0, w - 1) + "…" : text.slice(0, w)
    return frag(span(right ? lpad(text, w) : pad(text, w), color, bg), w)
  }
  function gap(n, bg) { return frag(span(rep(" ", n), fg, bg), n) }

  // Build-time accumulators: every emitted line registers its target.
  function put(html, target) {
    scratch.rows.push(target || null)
    if (target) scratch.jumps.push({ line: scratch.line, target: target })
    scratch.line++
    return html
  }
  function line(f, target, hl) {
    var bg = hl ? hilite : null
    var body = f.html + span(rep(" ", Math.max(0, inner - f.len)), fg, bg)
    return put(span("│ ", faint) + body + span(" │", faint) + "<br>", target)
  }
  function blank() { return line(frag("", 0)) }
  function rule(title, first, last) {
    var l = first ? "┌" : (last ? "└" : "├"), r = first ? "┐" : (last ? "┘" : "┤")
    if (!title) return put(span(l + rep("─", cols - 2) + r, faint) + "<br>")
    if (title.length > cols - 6) title = title.slice(0, cols - 7) + "…"
    return put(span(l + "─ ", faint) + span(title, dim) + span(" " + rep("─", cols - 5 - title.length) + r, faint) + "<br>")
  }

  // Is this jump the one under the cursor?
  function isCursor(idx) { return idx === cursor }

  function buildTui() {
    scratch.rows = []; scratch.jumps = []; scratch.line = 0
    var out = ""
    var c = counts
    var st = root.snap
    out += rule("HERDR" + (st ? " · " + (c.servers || 0) + (c.servers === 1 ? " server" : " servers") + " · " + (c.agents || 0) + " agents" : ""), true, false)

    if (!st) {
      out += line(cell("starting…", inner, dim))
      out += rule("", false, true)
      return finish(out)
    }
    if (servers.length === 0) {
      out += line(cell("no herdr session is attached from this desktop", inner, dim))
      out += line(cell("run herdr (or herdr --remote host) in a terminal", inner, faint))
      out += rule("", false, true)
      return finish(out)
    }

    // Summary: one cell per status, blocked first.
    out += line(cat(cell("■", 1, red), gap(1), cell((c.blocked || 0) + " need input", 13, (c.blocked || 0) > 0 ? fg : dim), gap(1),
                    cell("■", 1, yellow), gap(1), cell((c.working || 0) + " working", 11, (c.working || 0) > 0 ? fg : dim), gap(1),
                    cell("■", 1, green), gap(1), cell((c.done || 0) + " done", 9, (c.done || 0) > 0 ? fg : dim), gap(1),
                    cell("■", 1, grey), gap(1), cell((c.idle || 0) + " idle", 9, dim)))

    var jumpIdx = 0
    for (var s = 0; s < servers.length; s++) {
      var srv = servers[s], snap = srv.snapshot
      var where = srv.windows && srv.windows.length > 0 ? " · window on ws " + srv.windows[0].workspace : " · no window"
      var title = "@ " + srv.label + (srv.session !== "default" ? " · " + srv.session : "")
      if (snap) title += " · " + snap.workspaces.length + " spaces · " + snap.panes.length + " panes"
      out += rule(title + where, false, false)
      if (!srv.ok || !snap) {
        out += line(cell("✗ " + (srv.error || "unavailable"), inner, urgent))
        continue
      }
      if (!srv.connected) out += line(cell("events disconnected · polling", inner, faint))
      var part = viewMode === "agents" ? agentsView(srv, jumpIdx) : spacesView(srv, jumpIdx)
      out += part.html
      jumpIdx = part.jumpIdx
    }

    out += rule("", false, false)
    out += line(cell("j/k move · ⏎ jump · / filter · h hide · R refresh", inner, dim))
    out += line(cell("v view " + viewMode + " · r bar " + barMetric + " · i icon " + barIconName + (scrub ? " · hidden" : ""), inner, fg))
    if (filtering || filter) out += line(cat(cell("/ " + filter, inner - 2, accent), cell(filtering ? "▏" : "", 2, accent)))
    out += rule("", false, true)
    return finish(out)
  }

  function finish(out) {
    rows = scratch.rows; jumps = scratch.jumps
    checkWidths(out)
    // A trailing <br> renders as an empty line and pads the card.
    return out.replace(/<br>$/, "")
  }

  function spaceName(snap, wsId) {
    for (var i = 0; i < snap.workspaces.length; i++) if (snap.workspaces[i].workspace_id === wsId) return snap.workspaces[i]
    return null
  }
  function tabName(snap, tabId) {
    for (var i = 0; i < snap.tabs.length; i++) if (snap.tabs[i].tab_id === tabId) return snap.tabs[i]
    return null
  }

  // Agents: glyph 1 · agent 9 · space › tab 25 · status 11 · since 6  (=56)
  function agentsView(srv, jumpIdx) {
    var out = "", snap = srv.snapshot
    var agents = snap.agents.slice()
    var since = srv.since || ({})
    agents.sort(function(a, b) {
      var pa = statusOrder[a.agent_status] !== undefined ? statusOrder[a.agent_status] : 9
      var pb = statusOrder[b.agent_status] !== undefined ? statusOrder[b.agent_status] : 9
      if (pa !== pb) return pa - pb
      return (since[b.pane_id] || 0) - (since[a.pane_id] || 0)
    })
    out += line(cat(gap(2), cell("agent", 9, faint), gap(1), cell("space › tab", 25, faint), gap(1), cell("status", 11, faint), gap(1), cell("since", 6, faint, true)))
    if (agents.length === 0) out += line(cat(gap(2), cell("no agents running", inner - 2, dim)))
    var shown = 0
    for (var i = 0; i < agents.length; i++) {
      var a = agents[i], st = a.agent_status || "unknown"
      var ws = spaceName(snap, a.workspace_id), tab = tabName(snap, a.tab_id)
      var wsLabel = ws ? ws.label : a.workspace_id, tabLabel = tab ? tab.label : a.tab_id
      if (!matches((a.name || "") + " " + a.agent + " " + wsLabel + " " + tabLabel + " " + statusText(st))) continue
      shown++
      var loc = label(wsLabel) + " › " + label(tabLabel)
      var target = { server: srv.key, kind: "pane", id: a.pane_id }
      var hl = isCursor(jumpIdx++)
      var col = statusColor(st), active = st !== "idle"
      out += line(cat(cell(statusGlyph(st), 1, col, false, hl ? hilite : null), gap(1, hl ? hilite : null),
                      cell(a.name ? label(a.name) : a.agent, 9, active ? fg : dim, false, hl ? hilite : null), gap(1, hl ? hilite : null),
                      cell(loc, 25, active ? fg : dim, false, hl ? hilite : null), gap(1, hl ? hilite : null),
                      cell(statusText(st), 11, col, false, hl ? hilite : null), gap(1, hl ? hilite : null),
                      cell(fmtSince(since[a.pane_id]), 6, dim, true, hl ? hilite : null)), target, hl)
    }
    if (agents.length > 0 && shown === 0) out += line(cat(gap(2), cell("nothing matches “" + filter + "”", inner - 2, dim)))
    return { html: out, jumpIdx: jumpIdx }
  }

  // Spaces: number 2 · label 22 · status 13 · tabs/agents 17; tabs indented.
  function spacesView(srv, jumpIdx) {
    var out = "", snap = srv.snapshot
    var since = srv.since || ({})
    var byWs = ({})
    for (var t = 0; t < snap.tabs.length; t++) {
      if (!byWs[snap.tabs[t].workspace_id]) byWs[snap.tabs[t].workspace_id] = []
      byWs[snap.tabs[t].workspace_id].push(snap.tabs[t])
    }
    var agentsByTab = ({})
    for (var a = 0; a < snap.agents.length; a++) {
      if (!agentsByTab[snap.agents[a].tab_id]) agentsByTab[snap.agents[a].tab_id] = []
      agentsByTab[snap.agents[a].tab_id].push(snap.agents[a])
    }
    var shownSpaces = 0
    for (var w = 0; w < snap.workspaces.length; w++) {
      var ws = snap.workspaces[w], st = ws.agent_status || ""
      var tabs = byWs[ws.workspace_id] || []
      var nAgents = 0
      for (var k = 0; k < tabs.length; k++) nAgents += (agentsByTab[tabs[k].tab_id] || []).length
      // Filter: a space that matches shows every tab; otherwise only matching tabs.
      var wsMatch = matches(ws.label)
      if (filter && !wsMatch) {
        tabs = tabs.filter(function(t) {
          var ag0 = agentsByTab[t.tab_id] || [], text = t.label + " " + statusText(t.agent_status || "")
          for (var q = 0; q < ag0.length; q++) text += " " + (ag0[q].name || "") + " " + ag0[q].agent + " " + statusText(ag0[q].agent_status || "")
          return matches(text)
        })
        if (tabs.length === 0) continue
      }
      shownSpaces++
      var info = tabs.length + (tabs.length === 1 ? " tab" : " tabs") + (nAgents > 0 ? " · " + nAgents + (nAgents === 1 ? " agent" : " agents") : "")
      var hl = isCursor(jumpIdx++)
      var bg = hl ? hilite : null
      var focused = snap.focused_workspace_id === ws.workspace_id
      var wsActive = nAgents > 0 && st !== "idle" && st !== ""
      out += line(cat(cell(ws.number, 2, focused ? accent : dim, true, bg), gap(1, bg),
                      cell(label(ws.label), 22, focused ? accent : (wsActive || nAgents > 0 ? fg : dim), false, bg), gap(1, bg),
                      cell(nAgents > 0 ? statusGlyph(st) + " " + statusText(st) : "", 13, statusColor(st), false, bg), gap(1, bg),
                      cell(info, 17, dim, false, bg)), { server: srv.key, kind: "ws", id: ws.workspace_id }, hl)
      for (var i = 0; i < tabs.length; i++) {
        var tab = tabs[i], tst = tab.agent_status || ""
        var ag = agentsByTab[tab.tab_id] || []
        var names = [], newest = 0
        for (var j = 0; j < ag.length; j++) { names.push(ag[j].name ? label(ag[j].name) : ag[j].agent); newest = Math.max(newest, since[ag[j].pane_id] || 0) }
        var thl = isCursor(jumpIdx++)
        var tbg = thl ? hilite : null
        var tfocused = snap.focused_tab_id === tab.tab_id
        var tActive = ag.length > 0 && tst !== "idle"
        out += line(cat(gap(2, tbg), cell(i === tabs.length - 1 ? "└" : "├", 1, faint, false, tbg), gap(1, tbg),
                        cell(label(tab.label), 22, tfocused ? accent : (tActive ? fg : dim), false, tbg), gap(1, tbg),
                        cell(ag.length > 0 ? statusGlyph(tst) + " " + statusText(tst) : "", 13, statusColor(tst), false, tbg), gap(1, tbg),
                        cell(names.join(" "), 9, tActive ? fg : dim, false, tbg), gap(1, tbg),
                        cell(newest ? fmtSince(newest) : "", 5, dim, true, tbg)), { server: srv.key, kind: "tab", id: tab.tab_id }, thl)
      }
    }
    if (snap.workspaces.length > 0 && shownSpaces === 0) out += line(cat(gap(2), cell("nothing matches “" + filter + "”", inner - 2, dim)))
    return { html: out, jumpIdx: jumpIdx }
  }

  function checkWidths(html) {
    var rowsHtml = html.split("<br>")
    for (var i = 0; i < rowsHtml.length; i++) {
      if (rowsHtml[i] === "") continue
      var plain = rowsHtml[i].replace(/<[^>]+>/g, "").replace(/&nbsp;/g, " ").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&")
      if (plain.length !== cols) console.warn("omaherdr", "line " + i + " is " + plain.length + " wide, want " + cols + ":", plain)
    }
  }

  // ---------------------------------------------------------------- cursor
  function moveCursor(delta) {
    if (jumps.length === 0) return
    cursor = Math.max(0, Math.min(jumps.length - 1, cursor + delta))
    ensureVisible(jumps[cursor].line)
  }
  function lineHeightPx() { return rows.length > 0 ? tuiText.height / rows.length : 0 }
  function ensureVisible(lineIdx) {
    var lh = lineHeightPx(), top = lineIdx * lh + tuiText.y, bottom = top + lh
    if (top < panelFlick.contentY) panelFlick.contentY = Math.max(0, top - lh)
    else if (bottom > panelFlick.contentY + panelFlick.height) panelFlick.contentY = Math.min(panelFlick.contentHeight - panelFlick.height, bottom - panelFlick.height + lh)
  }
  function jumpAtY(y) {
    var lh = lineHeightPx()
    if (lh <= 0) return -1
    var lineIdx = Math.floor((y - tuiText.y) / lh)
    for (var i = 0; i < jumps.length; i++) if (jumps[i].line === lineIdx) return i
    return -1
  }
  function activateCursor() {
    if (jumps.length === 0) return
    go(jumps[Math.min(cursor, jumps.length - 1)].target)
  }

  // ---------------------------------------------------------------- bar
  // Lights, top to bottom like a traffic light. Icon-only mode stacks the
  // lit ones beside the icon; the number modes give each its own count.
  readonly property var lightOrder: ["blocked", "working", "done", "idle"]
  function lightsFor(keys) {
    var out = []
    for (var i = 0; i < lightOrder.length; i++) {
      var k = lightOrder[i]
      if (keys.indexOf(k) >= 0 && (counts[k] || 0) > 0) out.push({ key: k, count: counts[k], color: statusColor(k) })
    }
    return out
  }
  readonly property var stackLights: snap ? lightsFor(["blocked", "working", "done"]) : []
  readonly property var comboLights: !snap || barMetric === "none" ? [] :
    lightsFor(barMetric === "all" ? ["blocked", "working", "done", "idle"] : ["blocked", "done"])
  readonly property int dot: Math.round(Style.font.caption * 0.55)
  readonly property string barTooltip: {
    var c = counts
    if (!snap) return "Omaherdr"
    if (!c.agents) return "Omaherdr · no agents"
    return c.agents + " agents · " + (c.blocked || 0) + " need input · " + (c.done || 0) + " done · " + (c.working || 0) + " working · " + (c.idle || 0) + " idle"
  }

  implicitWidth: row.implicitWidth
  implicitHeight: bar ? bar.barSize : Style.bar.sizeHorizontal

  Row {
    id: row
    anchors.centerIn: parent

    // Icon-only mode: a vertical stack of the lit lights, left of the icon.
    Item {
      id: stack
      visible: root.barMetric === "none" && root.stackLights.length > 0 && !(root.bar && root.bar.vertical)
      width: visible ? root.dot + Style.space(4) : 0
      height: row.height
      Column {
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        Repeater {
          model: root.stackLights
          Rectangle { width: root.dot; height: root.dot; radius: 1; color: modelData.color }
        }
      }
    }

    BarIconButton {
      id: button
      bar: root.bar
      text: root.barIcon
      onPressed: function(buttonCode) { root.barPressed(buttonCode) }
    }

    // Number modes: one light and its count per state, spaced to scan.
    Item {
      id: metric
      visible: !(root.bar && root.bar.vertical) && root.comboLights.length > 0
      width: visible ? combos.width + Style.space(6) : 0
      height: row.height

      Row {
        id: combos
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(8)
        Repeater {
          model: root.comboLights
          Row {
            spacing: Style.space(4)
            Rectangle { anchors.verticalCenter: parent.verticalCenter; width: root.dot; height: root.dot; radius: 1; color: modelData.color }
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: modelData.count
              color: root.bar ? root.bar.barForeground : root.fg
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }
        }
      }

      MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        acceptedButtons: Qt.LeftButton | Qt.MiddleButton | Qt.RightButton
        onClicked: function(mouse) { root.barPressed(mouse.button) }
        onEntered: if (root.bar) root.bar.showTooltip(metric, root.barTooltip)
        onExited: if (root.bar) root.bar.hideTooltip(metric)
      }
    }
  }

  function barPressed(buttonCode) {
    if (buttonCode === Qt.RightButton) root.refresh()
    else if (buttonCode === Qt.MiddleButton) root.toggleView()
    else root.toggle()
  }

  // ---------------------------------------------------------------- panel
  TextMetrics {
    id: colMetrics
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
    text: root.rep("─", root.cols)
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(colMetrics.advanceWidth + panel.padding * 2 + Style.space(12))
    contentHeight: panel.fittedContentHeight(tuiText.implicitHeight + Style.space(8), Style.space(920))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent

      onMoveRequested: function(dx, dy) {
        if (dx < 0) root.scrub = !root.scrub
        if (dy !== 0) root.moveCursor(dy)
      }
      onActivateRequested: root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "/") root.startFilter()
        else if (t === "r") root.cycleBarMetric()
        else if (t === "R") root.refresh()
        else if (t === "v" || t === "V") root.toggleView()
        else if (t === "i" || t === "I") root.cycleBarIcon()
        else if (t === "g") { root.cursor = 0; panelFlick.contentY = 0 }
        else if (t === "G") { root.moveCursor(root.jumps.length) }
      }

      // While filtering every printable key is text; Esc clears/leaves, Enter keeps the filter.
      Item {
        id: filterCatcher
        anchors.fill: parent
        focus: root.filtering
        Keys.onPressed: function(event) {
          if (event.key === Qt.Key_Escape) { root.stopFilter(true); event.accepted = true; return }
          if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) { root.stopFilter(false); event.accepted = true; return }
          if (event.key === Qt.Key_Backspace) { root.filter = root.filter.slice(0, -1); event.accepted = true; return }
          if (event.key === Qt.Key_Down) { root.moveCursor(1); event.accepted = true; return }
          if (event.key === Qt.Key_Up) { root.moveCursor(-1); event.accepted = true; return }
          if (event.text && event.text.length === 1 && event.text >= " ") { root.filter += event.text; event.accepted = true }
        }
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: tuiText.implicitHeight + Style.space(8)
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Text {
          id: tuiText
          x: Style.space(4)
          y: Style.space(4)
          text: root.tui
          textFormat: Text.RichText
          color: root.fg
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          lineHeight: 1.15
          wrapMode: Text.NoWrap
        }

        MouseArea {
          anchors.fill: parent
          hoverEnabled: true
          cursorShape: root.jumpAtY(mouseY) >= 0 ? Qt.PointingHandCursor : Qt.ArrowCursor
          onPositionChanged: function(mouse) {
            var j = root.jumpAtY(mouse.y)
            if (j >= 0) root.cursor = j
          }
          onClicked: function(mouse) {
            var j = root.jumpAtY(mouse.y)
            if (j >= 0) root.go(root.jumps[j].target)
          }
        }
      }
    }
  }
}
