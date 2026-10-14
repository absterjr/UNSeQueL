// UNSeQueL for VS Code: run .unsequel files like SQL clients run .sql files.
// Commands pipe `unsequel run <file> [--lines A:B]` into a terminal, so the file's
// own `# source` directives supply the connection and no flags are needed.

const path = require("path");
const vscode = require("vscode");

function executable() {
  return vscode.workspace.getConfiguration("unsequel").get("executable", "unsequel");
}

let terminal = null;

function unsequelTerminal() {
  if (terminal === null || terminal.exitStatus !== undefined) {
    terminal = vscode.window.createTerminal({ name: "UNSeQueL" });
  }
  return terminal;
}

function quote(text) {
  return `"${text.replace(/"/g, '\\"')}"`;
}

/** Save the document (so line numbers match disk) and return true on success. */
async function ensureSaved(document) {
  if (!document.isDirty && !document.isUntitled) return true;
  const saved = await document.save();
  if (!saved) {
    void vscode.window.showWarningMessage("UNSeQueL: save the file before running it.");
  }
  return saved;
}

async function runCommand(includeSelection) {
  const editor = vscode.window.activeTextEditor;
  if (editor === undefined) {
    void vscode.window.showWarningMessage("UNSeQueL: open a .unsequel file first.");
    return;
  }
  const document = editor.document;
  if (document.languageId !== "unsequel") {
    void vscode.window.showWarningMessage("UNSeQueL: the active editor is not a .unsequel file.");
    return;
  }
  if (!(await ensureSaved(document))) return;

  const binary = executable();
  const file = quote(path.normalize(document.uri.fsPath));
  let command = `${binary} run ${file}`;

  if (includeSelection) {
    const selection = editor.selection;
    let startLine = selection.start.line + 1;
    let endLine = selection.end.line + 1;
    if (endLine > startLine && selection.end.character === 0) endLine -= 1;
    command += ` --lines ${startLine}:${endLine}`;
  }

  const target = unsequelTerminal();
  target.show();
  target.sendText(command, true);
}

function openRepl() {
  const target = unsequelTerminal();
  target.show();
  target.sendText(`${executable()} repl`, true);
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("unsequel.runSelection", () => runCommand(true)),
    vscode.commands.registerCommand("unsequel.runFile", () => runCommand(false)),
    vscode.commands.registerCommand("unsequel.openRepl", () => openRepl()),
  );
}

function deactivate() {
  if (terminal !== null) {
    terminal.dispose();
    terminal = null;
  }
}

module.exports = { activate, deactivate };
