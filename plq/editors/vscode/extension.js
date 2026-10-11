// PLQ for VS Code: run .plq files like SQL clients run .sql files.
// Commands pipe `plq run <file> [--lines A:B]` into a terminal, so the file's
// own `# source` directives supply the connection and no flags are needed.

const path = require("path");
const vscode = require("vscode");

function executable() {
  return vscode.workspace.getConfiguration("plq").get("executable", "plq");
}

let terminal = null;

function plqTerminal() {
  if (terminal === null || terminal.exitStatus !== undefined) {
    terminal = vscode.window.createTerminal({ name: "PLQ" });
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
    void vscode.window.showWarningMessage("PLQ: save the file before running it.");
  }
  return saved;
}

async function runCommand(includeSelection) {
  const editor = vscode.window.activeTextEditor;
  if (editor === undefined) {
    void vscode.window.showWarningMessage("PLQ: open a .plq file first.");
    return;
  }
  const document = editor.document;
  if (document.languageId !== "plq") {
    void vscode.window.showWarningMessage("PLQ: the active editor is not a .plq file.");
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

  const target = plqTerminal();
  target.show();
  target.sendText(command, true);
}

function openRepl() {
  const target = plqTerminal();
  target.show();
  target.sendText(`${executable()} repl`, true);
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("plq.runSelection", () => runCommand(true)),
    vscode.commands.registerCommand("plq.runFile", () => runCommand(false)),
    vscode.commands.registerCommand("plq.openRepl", () => openRepl()),
  );
}

function deactivate() {
  if (terminal !== null) {
    terminal.dispose();
    terminal = null;
  }
}

module.exports = { activate, deactivate };
