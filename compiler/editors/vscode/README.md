# UNSeQueL for VS Code

Syntax highlighting and run commands for `.unsequel` files — run queries like SQL
clients run `.sql` files.

| Command (Ctrl+Shift+P) | What it does |
| --- | --- |
| `UNSeQueL: Run Selection` | runs the selected lines with `unsequel run <file> --lines A:B` |
| `UNSeQueL: Run File` | runs the whole file with `unsequel run <file>` |
| `UNSeQueL: Open REPL` | opens an interactive `unsequel repl` terminal |

The file's own `# source name: path` directives supply the connection, so no
flags are needed. Both run commands are also in the editor context menu.

## Install

Until the extension is on the Marketplace, install it from this folder.

**Developer mode (fastest):** open the `compiler/editors/vscode` folder in VS Code
and press `F5` — a second VS Code window opens with the extension loaded.

**Local install (permanent):**

```bash
cd compiler/editors/vscode
npx @vscode/vsce package
code --install-extension unsequel-vscode-0.2.0.vsix
```

**Manual copy:** copy `editors/vscode/` into your extensions directory
(`%USERPROFILE%\.vscode\extensions\unsequel-vscode-0.2.0` on Windows,
`~/.vscode/extensions/unsequel-vscode-0.2.0` elsewhere) and restart VS Code.

## Without the extension

`unsequel repl` plus **Terminal: Run Selected Text in Active Terminal** gives the
same select-and-run flow: the selected stages paste into the REPL and execute
on a blank line.

## Settings

- `unsequel.executable` — the `unsequel` command to run; set an absolute path
  if it is not on `PATH`.

## Uninstall

```bash
code --uninstall-extension unsequel.unsequel-vscode
```
