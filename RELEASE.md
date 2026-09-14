Omaherdr v1.1.0 improves jumps when several terminal windows, tabs or panes host herdr.

## New

- Jumps now activate the hosting kitty tab/pane or WezTerm pane after focusing its window.
- Choose a preferred terminal with `omarchy bar set njpatel.omaherdr preferTerminal ghostty` (or another terminal class). The default, `auto`, uses the automatic window ranking.

## Fixes

- Select the right window when terminals such as Ghostty or `foot --server` share one process across several windows. Automatic selection uses herdr's focused-space title, then the host, then a dedicated window, the current workspace and recent focus.
- Prefer a server's dedicated window over a shared window on the current workspace, so a server on another monitor remains reachable. Contributed by @luizbafilho in [#1](https://github.com/njpatel/omaherdr/pull/1).
- Use kitty and WezTerm tab hints only when they match the terminal actually hosting the client, avoiding inherited hints from a parent terminal.
- Register the plugin's IPC target only on the mounted bar widget, preventing duplicate instances from silently losing calls.

## Packaging and documentation

- Include a root-level marketplace preview and remove generated Python bytecode from the repository.
- Document terminal support and limitations, dependencies, removal and contribution review.

Requires herdr 0.8 or newer and Python 3. Remote sessions still require non-interactive SSH access and Python 3 on the remote host. Ghostty jumps focus its window but cannot activate a hidden tab.

Contributors: @njpatel and @luizbafilho.

## Known limitation

- A named session created just after an empty discovery scan can appear unavailable because the daemon selects the default session's socket. Restarting the Omarchy shell after the session is running restores discovery. This existing issue is not fixed in v1.1.0.

**Full changelog:** https://github.com/njpatel/omaherdr/compare/v1.0.0...v1.1.0
