# UNSeQueL PLQ for VS Code

Syntax highlighting and run commands for `.plq` files — run queries like SQL
clients run `.sql` files.

| Command (Ctrl+Shift+P) | What it does |
| --- | --- |
| `PLQ: Run Selection` | runs the selected lines with `plq run <file> --lines A:B` |
| `PLQ: Run File` | runs the whole file with `plq run <file>` |
| `PLQ: Open REPL` | opens an interactive `plq repl` terminal |

The file's own `# source name: path` directives supply the connection, so no
flags are needed. Both run commands are also in the editor context menu.

## Install

Until the extension is on the Marketplace, install it from this folder.

**Developer mode (fastest):** open the `plq/editors/vscode` folder in VS Code
and press `F5` — a second VS Code window opens with the extension loaded.

**Local install (permanent):**

```bash
cd plq/editors/vscode
npx @vscode/vsce package --allow-missing-repository
code --install-extension plq-vscode-0.2.0.vsix
```

**Manual copy:** copy `editors/vscode/` into your extensions directory
(`%USERPROFILE%\.vscode\extensions\plq-vscode-0.2.0` on Windows,
`~/.vscode/extensions/plq-vscode-0.2.0` elsewhere) and restart VS Code.

## Without the extension

`plq repl` plus **Terminal: Run Selected Text in Active Terminal** gives the
same select-and-run flow: the selected stages paste into the REPL and execute
on a blank line.

## Settings

- `plq.executable` — the `plq` command to run; set an absolute path if `plq`
  is not on `PATH`.

## Uninstall

```bash
code --uninstall-extension unsequel.plq-vscode
```
