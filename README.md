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

`v` cycles agents, spaces and attention. Attention lists agents waiting for input or done, including snoozed, muted and last-known offline entries. `s` snoozes the selected item, `S` restores it, and `m` mutes or unmutes its workspace. Muted workspaces stay in the list so you can unmute them later. Ages mean when Omaherdr noticed a state, not when an agent asked a question.

Desktop notifications are **off by default**. Press `n` in the panel to switch them on or off, use **Desktop alerts** in the widget settings, or set them directly:

```sh
omarchy bar set njpatel.omaherdr notifications true
omarchy bar set njpatel.omaherdr notifications false
```

Switching them off stops new alerts without hiding the bar lights or clearing attention. Cards already kept by Omapager, and notification history, belong to the notification service; dismiss them there. Omaherdr does not change herdr's own toast setting. Turn those off in herdr if you want only Omaherdr's notifications.

An agent needing input gets one alert; agents finishing together are grouped by server and workspace. The icon is your chosen bar icon, centred in a box tinted red for input, green for done or grey for connection loss. The text names the workspace, agent and any meaningful tab name, rather than a bare tab number. Herdr 0.9 has no structured last-message field, so Omaherdr shows state and location instead of scraping terminal text or reading session files.

Click to return to the agent's pane (the first listed agent for a group). If there is no known dedicated terminal window, or the target has gone away, the attention list opens instead. Connection alerts open that list too. Focusing a done agent can acknowledge it through herdr; dismissing a notification cannot. Nothing here answers or approves an agent's request.

Omapager offers **Open in app** for that action. To make clicking anywhere on its card do the same, enable the setting below. It applies to **all senders**, not just Omaherdr; Omaherdr does not switch it on for you. Omapager owns its notification snooze, mute and history controls.

```sh
omarchy bar set njpatel.omapager allowDefaultActionOnCardClick true --json
```

To also watch enabled herdr 0.9 saved machines without an attached terminal:

```sh
omarchy bar set njpatel.omaherdr watchSavedMachines true
```

That uses the saved SSH profiles, which must already work non-interactively. It neither starts servers nor switches machines inside a combined herdr client.

| setting | default | behaviour |
|---|---|---|
| `notifications` | `false` | desktop popups; disabling keeps the attention list |
| `notifyDone` | `true` | include completion popups |
| `completionDelaySec` | `3` | completion grouping window, 1–30 seconds |
| `snoozeMinutes` | `10` | snooze duration, 1–120 minutes |
| `quietStart`, `quietEnd` | empty | local `HH:MM`; supports overnight ranges; equal or unset values disable quiet hours |
| `watchSavedMachines` | `false` | monitor enabled herdr 0.9 saved profiles |

Set each with `omarchy bar set njpatel.omaherdr SETTING VALUE`. Quiet hours hold back popups, not attention. Startup, reconnect and switching alerts on do not replay everything already pending. A sustained connection failure produces one alert for the session, not one per agent. Missing notification services are shown in the panel; ordinary discovery still works.

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

The local `bin/omaherdr-notify` helper tracks attention and sends desktop notifications over D-Bus. Widget copies share one publisher, so multiple bars do not produce duplicate alerts. It saves snoozes, workspace mutes and delivery history locally, and renders the selected icon in memory using the current theme. Native notification clicks are tied to current agent identities. The stock Omarchy renderer also keeps a click command across helper restarts; Omapager's native actions depend on a live sender. Neither helper reads agent transcripts or sends answers.

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
