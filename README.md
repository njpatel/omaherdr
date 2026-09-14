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

Remove with `omarchy plugin remove njpatel.omaherdr`; it leaves nothing behind except `~/.local/state/omarchy/omaherdr/` (delete it if you like). Needs herdr 0.8 or newer and `python3`; for `--remote` hosts, an SSH key that works non-interactively and `python3` there too. No other dependencies.

Local and standalone `--remote` discovery, live status and jumps have been checked with herdr 0.9.0 and foot 1.27.0, including a real OpenCode 1.18.30 agent completing background work and changing from done to idle when its row is clicked. Snapshot and event handling also work with herdr 0.8.2.

Herdr 0.9's saved machines (`herdr machine add`) are not discovered or selected by Omaherdr: use a separate `herdr --remote HOST` client for remote sessions. When a combined client is showing a remote machine, even clicking a Local workspace in Omaherdr does not switch that client back to Local. The public focus API is session-wide, so a jump also changes other clients viewing that server rather than preserving their independent views.

## Use

The bar shows the icon with traffic lights: red for agents waiting for input, yellow for working, green for done, grey for idle (colours come from your theme). By default each lit state gets a light and its count (`attention` = red and green; `active` adds yellow; `all` adds grey); in icon-only mode the lit lights stack beside the icon. No lights means nothing needs you. Click the icon or the counts to open the panel.

![Bar states](assets/bar.png)

| key | |
|---|---|
| `j` `k` | move |
| `/` | filter by space, tab, agent or status; `Esc` clears |
| `Enter` / click | jump: focuses the terminal window, then the space, tab or agent inside herdr |
| `v` | agents (most urgent first) / spaces (every space with its tabs) |
| `h` | redact names |
| `r` | cycle what the bar shows: attention, active, all, none |
| `l` | lights / inverse: a square beside each count, or the count on a pill of that colour (icon-only mode paints the colours behind the icon) |
| `i` | cycle the bar icon |
| `R` | refresh |

Status is live: the daemon subscribes to herdr's events, so the bar flips the moment an agent blocks on a question or finishes. `since` is how long the agent has been in its current state, as observed from here. Settings live on the bar entry: `omarchy bar set njpatel.omaherdr barMetric all` (or `barStyle`, `barIcon`, `view`, and `scanIntervalSec` for how often new or closed sessions are looked for, default 10).

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

These checks cover event delivery and status counts; they do not replace a real desktop and agent smoke test.

## Contributing

See [how we review contributions](CONTRIBUTING.md#how-we-review-contributions).

## License

Apache-2.0
