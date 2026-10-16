// UNSeQueL for VS Code: language support for .unsequel files.
//
// The extension bundles the compiled language server (`server/`, no DuckDB
// needed) so diagnostics, completion, hover (the SQL a stage compiles to),
// formatting, and the outline work with no separate download; run commands
// pipe `unsequel run <file> [--lines A:B]` into a terminal so the file's own
// `# source` directives supply the connection.

const fs = require("fs");
const os = require("os");
const path = require("path");
const vscode = require("vscode");
const { LanguageClient, TransportKind } = require("vscode-languageclient/node");

const RELEASES_URL = "https://github.com/absterjr/UNSeQueL/releases/latest";

let extensionContext = null;

function binaryName() {
  return process.platform === "win32" ? "unsequel.exe" : "unsequel";
}

function configuredExecutable() {
  const value = vscode.workspace.getConfiguration("unsequel").get("executable", "unsequel");
  return value === "unsequel" ? null : value;
}

/**
 * Folders to check for an unzipped run bundle, in preference order. Never the
 * workspace: binaries there get committed by accident.
 */
function bundleFolders() {
  const folders = [path.join(os.homedir(), "unsequel-run")];
  if (extensionContext !== null) {
    folders.push(path.join(extensionContext.globalStorageUri.fsPath, "unsequel-run"));
  }
  return folders;
}

/**
 * Resolve the CLI for run/repl commands: an explicit `unsequel.executable`
 * wins, then an unzipped run bundle in ~/unsequel-run or the extension's
 * storage folder, then PATH.
 */
function executable() {
  const configured = configuredExecutable();
  if (configured !== null) return configured;
  for (const folder of bundleFolders()) {
    const candidate = path.join(folder, binaryName());
    if (fs.existsSync(candidate)) return candidate;
  }
  return "unsequel";
}

/**
 * The extension ships the compiled server; it runs under VS Code's own Node
 * (ELECTRON_RUN_AS_NODE), so language features need nothing installed. Without
 * DuckDB it still parses, formats, outlines, and completes; column checks run
 * against a declared `# schema:`/--schema file.
 */
function bundledServerOptions() {
  const server = path.join(__dirname, "server", "main.js");
  if (!fs.existsSync(server)) return null;
  return {
    command: process.execPath,
    args: [server, "lsp"],
    transport: TransportKind.stdio,
    options: { env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" } },
  };
}

/** Server candidates in preference order: configured, run bundle, PATH, bundled. */
function serverCandidates() {
  const candidates = [];
  const configured = configuredExecutable();
  if (configured !== null) {
    return [{ command: configured, args: ["lsp"], transport: TransportKind.stdio }];
  }
  for (const folder of bundleFolders()) {
    const candidate = path.join(folder, binaryName());
    if (fs.existsSync(candidate)) {
      candidates.push({ command: candidate, args: ["lsp"], transport: TransportKind.stdio });
      break;
    }
  }
  candidates.push({ command: "unsequel", args: ["lsp"], transport: TransportKind.stdio });
  const bundled = bundledServerOptions();
  if (bundled !== null) candidates.push(bundled);
  return candidates;
}

function schemaSetting() {
  const value = vscode.workspace.getConfiguration("unsequel").get("schemaPath", "");
  return value === "" ? null : value;
}

let terminal = null;
let client = null;

async function startLanguageClient() {
  const clientOptions = {
    documentSelector: [{ language: "unsequel" }],
    initializationOptions: { schemaPath: schemaSetting() },
  };
  let lastError = null;
  for (const serverOptions of serverCandidates()) {
    const candidate = new LanguageClient("unsequel", "UNSeQueL", serverOptions, clientOptions);
    try {
      await candidate.start();
      client = candidate;
      return;
    } catch (error) {
      lastError = error;
      try {
        await candidate.stop();
      } catch {
        // never started
      }
    }
  }
  const pick = await vscode.window.showWarningMessage(
    "UNSeQueL: language features could not start. Set unsequel.executable to a "
    + "working CLI, or reinstall the extension (it bundles a server). "
    + `Details: ${lastError === null ? "no server candidate" : lastError.message}`,
    "Open Releases",
  );
  if (pick === "Open Releases") {
    void vscode.env.openExternal(vscode.Uri.parse(RELEASES_URL));
  }
}

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
  extensionContext = context;
  context.subscriptions.push(
    vscode.commands.registerCommand("unsequel.runSelection", () => runCommand(true)),
    vscode.commands.registerCommand("unsequel.runFile", () => runCommand(false)),
    vscode.commands.registerCommand("unsequel.openRepl", () => openRepl()),
  );
  void startLanguageClient();
}

async function deactivate() {
  if (terminal !== null) {
    terminal.dispose();
    terminal = null;
  }
  if (client !== null) {
    await client.stop();
    client = null;
  }
}

module.exports = { activate, deactivate };
