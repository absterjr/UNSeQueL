/**
 * UNSeQueL language server (stdio).
 *
 * Powers the VS Code integration with features the compiler already has the
 * pieces for: inline parse/semantic diagnostics, stage-keyword / source /
 * live-column completion, hover that shows the generated SQL for the stage
 * under the cursor, document formatting, and an outline of stages.
 *
 * Deliberately dependency-light: the compiler core plus the LSP protocol
 * library, no DuckDB.
 */

import { existsSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  CompletionItemKind,
  SymbolKind,
  TextDocumentSyncKind,
  createConnection,
  type CompletionItem,
  type Connection,
  type Diagnostic,
  type DocumentSymbol,
  type Hover,
  type InitializeParams,
  type InitializeResult,
  type Range,
  type TextEdit,
} from "vscode-languageserver/node.js";
import { TextDocuments } from "vscode-languageserver/node.js";
import { TextDocument } from "vscode-languageserver-textdocument";

import { type Program, type Stage } from "./ast.js";
import { emitSql } from "./codegen.js";
import { splitDirectives } from "./directives.js";
import { PlqError } from "./errors.js";
import { formatProgram } from "./format.js";
import { parseProgram } from "./parser.js";
import { loadSchema, type Schema } from "./schema.js";
import { analyze } from "./semantics.js";

const STAGE_KEYWORDS: readonly string[] = [
  "from", "join", "left join", "right join", "full join", "cross join",
  "filter", "derive", "group by", "aggregate", "having", "window", "select",
  "sort", "skip", "take", "distinct", "union", "union all", "intersect",
  "except", "sql", "with",
];

const SOURCE_PREFIX = /^(?:(?:left|right|full|cross)\s+join|join|from)\s+[A-Za-z_`]*$/;

interface Settings {
  schemaPath: string | null;
}

export function createUnsequelServer(connection: Connection): void {
  const documents = new TextDocuments(TextDocument);
  const settings: Settings = { schemaPath: null };

  const tryParse = (text: string): Program | null => {
    try {
      return parseProgram(text);
    } catch {
      return null;
    }
  };

  const schemaForDocument = (document: TextDocument): Schema | null => {
    const directives = splitDirectives(document.getText());
    if (directives.schemaPath !== null) {
      const path = documentPath(document, directives.schemaPath);
      if (existsSync(path)) {
        try {
          return loadSchema(path);
        } catch {
          return null;
        }
      }
      return null;
    }
    if (settings.schemaPath !== null && existsSync(settings.schemaPath)) {
      try {
        return loadSchema(settings.schemaPath);
      } catch {
        return null;
      }
    }
    return null;
  };

  const documentPath = (document: TextDocument, target: string): string => {
    if (isAbsolute(target) || !document.uri.startsWith("file:")) return target;
    return resolve(dirname(fileURLToPath(document.uri)), target);
  };

  const toRange = (stage: Stage): Range => ({
    start: { line: stage.span.start.line - 1, character: stage.span.start.column - 1 },
    end: { line: stage.span.end.line - 1, character: stage.span.end.column - 1 },
  });

  const errorDiagnostic = (error: PlqError): Diagnostic => {
    const line = Math.max((error.line ?? 1) - 1, 0);
    const character = Math.max((error.column ?? 1) - 1, 0);
    return {
      severity: 1,
      source: "unsequel",
      message: error.detail,
      range: { start: { line, character }, end: { line, character: character + 1 } },
    };
  };

  const validate = (document: TextDocument): void => {
    const diagnostics: Diagnostic[] = [];
    let program: Program | null = null;
    try {
      program = parseProgram(document.getText());
    } catch (error) {
      diagnostics.push(error instanceof PlqError
        ? errorDiagnostic(error)
        : { severity: 1, source: "unsequel", message: String(error),
            range: { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } } });
    }
    if (program !== null) {
      const schema = schemaForDocument(document);
      if (schema !== null) {
        try {
          analyze(program, schema);
        } catch (error) {
          if (error instanceof PlqError) diagnostics.push(errorDiagnostic(error));
        }
      }
    }
    connection.sendDiagnostics({ uri: document.uri, diagnostics });
  };

  /** The index (0-based) of the stage whose source line covers `line0`. */
  const stageIndexAtLine = (program: Program, line0: number): number => {
    const line = line0 + 1;
    let index = -1;
    for (let i = 0; i < program.length; i += 1) {
      const stage = program[i] as Stage;
      if (stage.span.start.line <= line) index = i;
      else break;
    }
    return index;
  };

  connection.onInitialize((params: InitializeParams): InitializeResult => {
    const options = params.initializationOptions as { schemaPath?: string | null } | null;
    settings.schemaPath = options?.schemaPath ?? null;
    return {
      capabilities: {
        textDocumentSync: TextDocumentSyncKind.Incremental,
        completionProvider: { resolveProvider: false, triggerCharacters: [" ", "."] },
        hoverProvider: true,
        documentFormattingProvider: true,
        documentSymbolProvider: true,
      },
    };
  });

  connection.onDidChangeConfiguration((params) => {
    const configuration = params.settings as { unsequel?: { schemaPath?: string | null } } | null;
    const path = configuration?.unsequel?.schemaPath;
    if (typeof path === "string" && path !== "") settings.schemaPath = path;
  });

  connection.onCompletion((params): CompletionItem[] => {
    const document = documents.get(params.textDocument.uri);
    if (document === undefined) return [];
    const lines = document.getText().split(/\r\n|\n/);
    const line = lines[params.position.line] ?? "";
    const before = line.slice(0, params.position.character);
    const trimmed = before.trimStart();

    if (trimmed === "") {
      return STAGE_KEYWORDS.map((keyword) => ({
        label: keyword,
        kind: CompletionItemKind.Keyword,
        detail: "stage",
      }));
    }

    if (SOURCE_PREFIX.test(trimmed)) {
      const items: CompletionItem[] = [];
      const schema = schemaForDocument(document);
      for (const name of schema?.tables.keys() ?? []) {
        items.push({ label: name, kind: CompletionItemKind.Class, detail: "table" });
      }
      const program = tryParse(document.getText());
      for (const stage of program ?? []) {
        if (stage.kind === "with") {
          items.push({ label: stage.name, kind: CompletionItemKind.Interface, detail: "with" });
        }
      }
      return items;
    }

    const program = tryParse(document.getText());
    const schema = schemaForDocument(document);
    if (program === null || schema === null) return [];
    const index = stageIndexAtLine(program, params.position.line);
    if (index < 0) return [];
    try {
      const lineage = analyze(program, schema);
      const columns = index === 0 ? [] : lineage[index - 1]?.columns ?? [];
      return columns.map((column) => ({
        label: column,
        kind: CompletionItemKind.Field,
        detail: "column",
      }));
    } catch {
      return [];
    }
  });

  connection.onHover((params): Hover | null => {
    const document = documents.get(params.textDocument.uri);
    if (document === undefined) return null;
    const program = tryParse(document.getText());
    if (program === null) return null;
    const index = stageIndexAtLine(program, params.position.line);
    if (index < 0) return null;
    const stage = program[index] as Stage;
    const schema = schemaForDocument(document);
    if (schema === null) {
      return {
        contents: { kind: "markdown", value: `**stage ${stage.index} (${stage.keyword})**` },
        range: toRange(stage),
      };
    }
    try {
      const lineage = analyze(program, schema);
      const sql = emitSql(program, schema, { stopAt: stage.index });
      const columns = lineage[index]?.columns ?? [];
      const value = [
        `**stage ${stage.index} (${stage.keyword})**`,
        "",
        "```sql",
        sql.trimEnd(),
        "```",
        "",
        columns.length > 0 ? `output columns: ${columns.join(", ")}` : "no output columns",
      ].join("\n");
      return { contents: { kind: "markdown", value }, range: toRange(stage) };
    } catch {
      return null;
    }
  });

  connection.onDocumentFormatting((params): TextEdit[] => {
    const document = documents.get(params.textDocument.uri);
    if (document === undefined) return [];
    let formatted: string;
    try {
      formatted = formatProgram(document.getText());
    } catch {
      return [];
    }
    if (formatted === document.getText()) return [];
    const lines = document.getText().split(/\r\n|\n/);
    return [{
      range: {
        start: { line: 0, character: 0 },
        end: { line: lines.length, character: 0 },
      },
      newText: formatted,
    }];
  });

  connection.onDocumentSymbol((params): DocumentSymbol[] => {
    const document = documents.get(params.textDocument.uri);
    if (document === undefined) return [];
    const program = tryParse(document.getText());
    if (program === null) return [];
    return program.map((stage) => {
      const range = toRange(stage);
      const name = stage.kind === "with"
        ? `with ${stage.name}`
        : `${stage.index} ${stage.keyword}`;
      return {
        name,
        kind: stage.kind === "with" ? SymbolKind.Interface : SymbolKind.Object,
        range,
        selectionRange: range,
      };
    });
  });

  documents.onDidOpen((change) => validate(change.document));
  documents.onDidChangeContent((change) => validate(change.document));
  documents.listen(connection);
  connection.listen();
}

/** Start the server on the process's stdio (the `unsequel lsp` command). */
export function startLspServer(): void {
  createUnsequelServer(createConnection(process.stdin, process.stdout));
}
