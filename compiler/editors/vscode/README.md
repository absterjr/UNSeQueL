# UNSeQueL for VS Code

Language support for `.unsequel` files — write and run queries like SQL clients
run `.sql` files.

## Language features

The extension bundles the compiled language server (`server/`), so diagnostics,
completion, hover, formatting, the outline, quick fixes, and go-to-definition
work with nothing else installed. Diagnostics that need data (`# source` /
`--db` column checks) use a discovered CLI when one exists — the bundled server
alone checks columns declared by a `# schema:` directive or `unsequel.schemaPath`.

| Feature | What it does |
| --- | --- |
| Diagnostics | parse and schema errors appear inline while you type (stage-located); a `# schema:` directive or `unsequel.schemaPath` enables semantic checks |
| Completion | stage keywords at a stage boundary, table/`with` names after `from`/`join`, and the live columns at the cursor |
| Hover | hover a stage to see the DuckDB SQL it compiles to and its output columns |
| Formatting | the canonical formatter (`unsequel fmt`); enable `editor.formatOnSave` for format-on-save |
| Outline | one symbol per stage (and per `with` definition) |
| Quick fixes | insert the required `aggregate` after `group by`; backtick a reserved identifier |
| Go to definition | jump from a `with` name (or a source using it) to its definition |

| Command (Ctrl+Shift+P) | What it does |
| --- | --- |
| `UNSeQueL: Run Selection` | runs the selected lines with `unsequel run <file> --lines A:B` |
| `UNSeQueL: Run File` | runs the whole file with `unsequel run <file>` |
| `UNSeQueL: Open REPL` | opens an interactive `unsequel repl` terminal |

The file's own `# source name: path` directives supply the connection, so no
flags are needed. Both run commands are also in the editor context menu.

## Requirements

None for language features (the server ships inside the extension). `run`/`repl`
need the `unsequel` CLI — from npm, a clone, or the GitHub releases:

```bash
npm install -g unsequel            # from npm (once published)
cd compiler && npm install && npm link   # from a clone
```

or unzip a run bundle into `~/unsequel-run` (the extension finds it), or point
`unsequel.executable` at the standalone compile executable
(`unsequel-compile-windows-x64.exe` etc.), which needs no Node and powers every
feature including source-aware checks.

## Install

Until the extension is on the Marketplace, install it from this folder.

**Developer mode (fastest):** open the `compiler/editors/vscode` folder in VS Code
and press `F5` — a second VS Code window opens with the extension loaded.

**Local install (permanent):**

```bash
cd compiler/editors/vscode
npm install
npm run vsix        # copies the compiled server in, then packages
code --install-extension unsequel-vscode-0.1.0.vsix
```

**Manual copy:** copy `editors/vscode/` into your extensions directory
(`%USERPROFILE%\.vscode\extensions\unsequel-vscode-0.1.0` on Windows,
`~/.vscode/extensions/unsequel-vscode-0.1.0` elsewhere) and restart VS Code.

## Without the extension

`unsequel repl` plus **Terminal: Run Selected Text in Active Terminal** gives the
same select-and-run flow: the selected stages paste into the REPL and execute
on a blank line.

## Settings

- `unsequel.executable` — the `unsequel` command for run/repl and (when set)
  the language server; set an absolute path if it is not on `PATH`. Leave it at
  `unsequel` to use the bundled server and a `~/unsequel-run` bundle if present.
- `unsequel.schemaPath` — schema JSON used for diagnostics and completion when
  a query has no `# schema:` directive.

## Uninstall

```bash
code --uninstall-extension unsequel.unsequel-vscode
```
