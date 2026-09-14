![Omaherdr](assets/title.png)

<!--
 ▄█████▄    ▄███████████▄   ▄███████   ▄██   ██▄  ▄████████  ▄███████▄  ▄███████▄  ▄███████▄
███   ███  ███   ███   ███  ███   ███  ███   ███  ███   ███  ███   ███  ███   ███  ███   ███
███   ███  ███   ███   ███  ███   ███  ███   ███  ███   ███  ███   ███  ███   ███  ███   ███
███   ███  ███   ███   ███  ███▄▄▄███  ███▄▄▄███  ███▄▄▄     ███▄▄▄██▀  ███   ███  ███▄▄▄██▀
███   ███  ███   ███   ███  ███▀▀▀███  ███▀▀▀███  ███▀▀▀     ███▀▀▀██▄  ███   ███  ███▀▀▀██▄
███   ███  ███   ███   ███  ███   ███  ███   ███  ███   ███  ███   ███  ███   ███  ███   ███
███   ███  ███   ███   ███  ███   ███  ███   ███  ███   ███  ███   ███  ███  ▄███  ███   ███
 ▀█████▀    ▀█   ███   █▀   ███   █▀    ▀█   █▀    ▀███████   ███   █▀  ▀███████▀   ███   █▀
-->

[herdr](https://herdr.dev) sessions, spaces, tabs and agents in the [Omarchy](https://omarchy.org) bar - with one keypress to jump to any of them.

![Omaherdr](assets/omaherdr.png)

## Install

```sh
omarchy plugin add https://github.com/njpatel/omaherdr.git --enable
omarchy restart shell
```

Nothing to configure. Omaherdr finds every `herdr` you are attached to from this desktop - plain, `--session NAME`, or `--remote HOST` - by looking at the processes in your terminal windows, then talks to each server over its socket (through `ssh HOST` for remote ones, which needs a key that works non-interactively and `python3` on the far side). A local server running with nobody attached is listed too.

Needs herdr 0.8 or newer and `python3`; remote hosts need non-interactive SSH access and `python3` too. The attention view and desktop notifications additionally use local PyGObject/Gio, Cairo and librsvg (`python-gobject`, `python-cairo`, `librsvg` on Arch). The remote helper remains Python-standard-library-only.

Local and standalone `--remote` discovery, live status and jumps have been checked with herdr 0.9.0 and foot 1.27.0, including a real OpenCode 1.18.30 agent completing background work and changing from done to idle when its row is clicked. Snapshot and event handling also work with herdr 0.8.2.

Herdr 0.9's enabled saved machines (`herdr machine add`) can be monitored with `watchSavedMachines` enabled below. This discovers their running sessions; it does not start servers or select a machine in a combined herdr client. When that client is showing a remote machine, even a legacy Local workspace jump does not switch it back to Local. Attention and notification actions only jump through a known dedicated window; otherwise they show the attention list. Herdr's focus API is session-wide, so a jump also changes other clients viewing that server.

## Use

The bar shows the icon with traffic lights: red for agents waiting for input, yellow for working, green for done, grey for idle (colours come from your theme). By default each lit state gets a light and its count (`attention` = red and green; `active` adds yellow; `all` adds grey); in icon-only mode the lit lights stack beside the icon. No lights means nothing needs you. Click the icon or the counts to open the panel.

![Bar states](assets/bar.png)

| key | |
|---|---|
| `j` `k` | move |
| `/` | filter by space, tab, agent or status; `Esc` clears |
| `Enter` / click | jump: focuses the terminal window, then the space, tab or agent inside herdr |
| `v` | cycle agents / spaces / attention |
| `n` | toggle desktop notifications |
| `s` / `S` | snooze / restore the selected attention item |
| `m` | mute / unmute the selected attention workspace |
| `h` | redact names |
| `r` | cycle what the bar shows: attention, active, all, none |
| `l` | lights / inverse: a square beside each count, or the count on a pill of that colour (icon-only mode paints the colours behind the icon) |
| `i` | cycle the bar icon |
| `R` | refresh |

Status is live: the daemon subscribes to herdr's events, so the bar flips the moment an agent blocks on a question or finishes. `since` is how long the agent has been in its current state, as observed from here. Settings live on the bar entry: `omarchy bar set njpatel.omaherdr barMetric all` (or `barStyle`, `barIcon`, `view`, and `scanIntervalSec` for how often new or closed sessions are looked for, default 10).

## Attention and notifications

Open the attention list with `v` or `omarchy-shell njpatel.omaherdr attention`. It retains agents needing input or marked done, including snoozed and muted entries. Muted workspaces remain selectable after their agents finish, so `m` can unmute them. Offline entries show the last known state, not a live agent; ages mean when Omaherdr noticed a state, not when a particular question was asked.

Desktop notifications are off by default. Enable them with `n` or:

```sh
omarchy bar set njpatel.omaherdr notifications true
omarchy bar set njpatel.omaherdr watchSavedMachines true
```

The second setting is optional and permits background SSH monitoring of enabled saved profiles, even without an attached terminal. Existing SSH trust and authentication must already work non-interactively. Omaherdr does not change herdr configuration or start replacement servers.

New needs-input episodes produce one alert; completions are grouped by server and workspace. Each toast provides only a default action: try opening the current agent (the first listed agent for a group), or show the attention list if a safe jump is unavailable. Connection alerts open the attention list. Dismissing a popup never answers or acknowledges a request. No separate snooze or mute actions are sent to the notification renderer; its own controls remain its responsibility. Existing attention-panel snooze and mute controls remain available.

The notification icon uses your selected `barIcon` and font inside a state-tinted box: theme red for needs-input, green for done and muted colour for connection loss. Icons are rendered locally in memory and sent as standard raw image data, not fetched from a website or written to icon files. The title names the state and workspace; the body keeps meaningful agent/tab names and remote/session context, omitting numeric tabs and duplicate labels.

Omapager may display the default action as its own **Open in app** button. Card-body clicks invoke it only when Omapager's `allowDefaultActionOnCardClick` setting is enabled; Omaherdr never changes that setting. The explicit button remains usable with it disabled. Enabling it applies to all notification senders, not just Omaherdr:

```sh
omarchy bar set njpatel.omapager allowDefaultActionOnCardClick true --json
```

The stock Omarchy renderer also receives a persisted click action using the same opaque attention identity, so its retained toast can still navigate after the original sender exits. Omapager uses live native actions instead; restoration and history behaviour belong to the renderer.

| setting | default | behaviour |
|---|---|---|
| `notifications` | `false` | desktop popups; disabling keeps the attention list |
| `notifyDone` | `true` | include completion popups |
| `completionDelaySec` | `3` | completion grouping window, 1–30 seconds |
| `snoozeMinutes` | `10` | snooze duration, 1–120 minutes |
| `quietStart`, `quietEnd` | empty | local `HH:MM`; supports overnight ranges; equal or unset values disable quiet hours |
| `watchSavedMachines` | `false` | monitor enabled herdr 0.9 saved profiles |

Set each with `omarchy bar set njpatel.omaherdr SETTING VALUE`. Quiet hours suppress popups, not the queue. Startup, reconnect and enabling notifications establish a baseline rather than replaying every pending item. A sustained connection failure produces one endpoint alert, not one per agent. Multiple widget copies share one notification publisher, preventing duplicate alerts.

Herdr 0.9 exposes terminal snapshots, but no structured last-assistant-message field. Omaherdr therefore uses the state and location context above; it does not scrape terminal UI text or read agent session files to invent a reply preview. Notifications contain no terminal transcripts, command previews, question text or inline replies/approvals. The desktop notification service's own history and do-not-disturb policy still apply. Missing attention or notification services are reported in the panel; ordinary discovery remains available.

## Removing

Use `omarchy plugin remove njpatel.omaherdr`. Plugin-owned helpers stop with the widget; herdr servers and clients are not stopped. No system service, keyring entry or credential is installed.

The state directory remains at `$XDG_STATE_HOME/omarchy/omaherdr/`, or `~/.local/state/omarchy/omaherdr/` by default. It contains `since.json` (observed status ages), `attention.json` (pending labels, delivery history, snoozes and workspace mutes), and `notification-transport.json` (notification IDs and server identity). Remove these files if you also want to forget that state. Notification history retained by the desktop notification service is separate and survives plugin removal according to that service's policy.

## Terminals

A jump first focuses the window that hosts your herdr client, then the tab or pane inside it where the terminal allows.

| terminal | jump lands on | tested |
|---|---|---|
| kitty 0.48 | window, then the exact tab/pane (via kitty remote control, on by default in Omarchy) | yes |
| foot 1.27 | window (foot has no tabs) | yes |
| Alacritty 0.17 | window (no tabs) | yes |
| Ghostty 1.3 | window; when several windows share one process the one titled by herdr is chosen. Tabs cannot be driven from outside, so a herdr tab that is not the active one stays behind | yes |
| WezTerm 2024-02 | window, then the exact pane via `wezterm cli` | yes |

Anything else gets the window. Windows are matched from the client's process tree, so it works for `herdr`, `herdr --session`, and `herdr --remote` alike, in any terminal Hyprland can see. All five were tested on Omarchy with Hyprland 0.56: launch, discovery, and a jump from another workspace. If more than one terminal window is attached to the same server, the jump picks by herdr's window title (focused space, then host), then prefers a window hosting only that server, then the current workspace, then most recent focus; set `preferTerminal` (`omarchy bar set njpatel.omaherdr preferTerminal ghostty`) to always win with one terminal instead.

## How it works

`bin/omaherdr-daemon` scans processes every 10 s, maps each herdr client to its Hyprland window, and runs one `bin/omaherdr-helper` per server (shipped inline over ssh for remote hosts). The helper subscribes to workspace, tab and pane events, waits for acknowledgement, then takes a `session.snapshot`. It adds per-pane agent-status subscriptions and takes another snapshot after each subscription change, covering changes while a stream is replaced. The daemon folds the snapshots and live events into one JSON state per change, which `Widget.qml` renders. Jumps go the other way: `focuswindow` in Hyprland, then the terminal tab that hosts the client (kitty via its remote control, WezTerm via `wezterm cli`; foot and Alacritty have no tabs, so the window is enough; Ghostty and `foot --server` run every window from one process, so the window is picked by its herdr title), then `workspace.focus` / `tab.focus` / `pane.focus` on the right server.

## Development

Run the focused event regressions with Python's standard library:

```sh
python3 -m unittest discover -s tests -v
```

These checks cover event delivery, notification episode boundaries, grouping, quiet hours, reconnection, snooze/mute persistence, navigation safety and private-state file handling. They do not replace a real desktop and agent smoke test.

## Contributing

See [how we review contributions](CONTRIBUTING.md#how-we-review-contributions).

## License

Apache-2.0
