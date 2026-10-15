import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough } from "node:stream";
import { pathToFileURL } from "node:url";
import { describe, expect, it } from "vitest";
import { createConnection, type Connection } from "vscode-languageserver/node.js";
import { createUnsequelServer } from "../src/lsp.js";

/**
 * Drives the language server over real JSON-RPC (Content-Length framing on
 * PassThrough streams) and asserts the headline capabilities: diagnostics,
 * completion, hover, formatting, and the document outline.
 */

interface ServerHarness {
  request(method: string, params: unknown): Promise<unknown>;
  notify(method: string, params: unknown): void;
  waitForNotification(method: string, matches?: (params: any) => boolean): Promise<any>;
}

function startServer(): ServerHarness {
  const clientToServer = new PassThrough();
  const serverToClient = new PassThrough();
  const connection: Connection = createConnection(clientToServer, serverToClient);
  createUnsequelServer(connection);

  let buffer = Buffer.alloc(0);
  let nextId = 1;
  const pending = new Map<number, { resolve: (value: unknown) => void }>();
  const notifications: Array<{ method: string; params: any }> = [];
  const waiters: Array<() => void> = [];

  serverToClient.on("data", (chunk: Buffer) => {
    buffer = Buffer.concat([buffer, chunk]);
    for (;;) {
      const headerEnd = buffer.indexOf("\r\n\r\n");
      if (headerEnd < 0) return;
      const header = buffer.subarray(0, headerEnd).toString("ascii");
      const match = /Content-Length: (\d+)/i.exec(header);
      if (match === null) {
        buffer = buffer.subarray(headerEnd + 4);
        continue;
      }
      const length = Number(match[1]);
      const start = headerEnd + 4;
      if (buffer.length < start + length) return;
      const payload = JSON.parse(buffer.subarray(start, start + length).toString("utf8"));
      buffer = buffer.subarray(start + length);
      if (payload.id !== undefined && (payload.result !== undefined || payload.error !== undefined)) {
        pending.get(payload.id)?.resolve(payload.result ?? payload.error);
        pending.delete(payload.id);
      } else if (typeof payload.method === "string") {
        notifications.push({ method: payload.method, params: payload.params });
        for (const wake of waiters.splice(0)) wake();
      }
    }
  });

  const send = (message: unknown): void => {
    const text = JSON.stringify(message);
    clientToServer.write(`Content-Length: ${Buffer.byteLength(text)}\r\n\r\n${text}`);
  };

  return {
    request(method, params) {
      const id = nextId++;
      send({ jsonrpc: "2.0", id, method, params });
      return new Promise((resolve) => pending.set(id, { resolve }));
    },
    notify(method, params) {
      send({ jsonrpc: "2.0", method, params });
    },
    async waitForNotification(method, matches) {
      const deadline = Date.now() + 3000;
      for (;;) {
        const found = notifications.find(
          (entry) => entry.method === method && (matches === undefined || matches(entry.params)));
        if (found !== undefined) return found.params;
        if (Date.now() > deadline) throw new Error(`no ${method} notification arrived`);
        await new Promise<void>((resolve) => {
          waiters.push(resolve);
          setTimeout(resolve, 25);
        });
      }
    },
  };
}

const QUERY = [
  "# schema: schema.json",
  "from orders",
  "filter quantity > 1",
  "select order_id, customer",
  "",
].join("\n");

function workspace(query = QUERY): { uri: string; directory: string } {
  const directory = mkdtempSync(join(tmpdir(), "unsequel-lsp-"));
  writeFileSync(join(directory, "schema.json"), JSON.stringify({
    tables: {
      orders: { columns: { order_id: "integer", customer: "string", quantity: "integer" } },
    },
  }), "utf8");
  writeFileSync(join(directory, "query.unsequel"), query, "utf8");
  return { uri: pathToFileURL(join(directory, "query.unsequel")).href, directory };
}

async function open(server: ServerHarness, uri: string, text: string) {
  server.notify("textDocument/didOpen", {
    textDocument: { uri, languageId: "unsequel", version: 1, text },
  });
  return server.waitForNotification("textDocument/publishDiagnostics",
    (params: any) => params.uri === uri);
}

async function initialized(): Promise<ServerHarness> {
  const server = startServer();
  await server.request("initialize", { processId: null, rootUri: null, capabilities: {} });
  return server;
}

describe("language server", () => {
  it("advertises the headline capabilities", async () => {
    const server = await initialized();
    const result = (await server.request("initialize", {
      processId: null, rootUri: null, capabilities: {},
    })) as any;
    expect(result.capabilities.hoverProvider).toBe(true);
    expect(result.capabilities.documentFormattingProvider).toBe(true);
    expect(result.capabilities.documentSymbolProvider).toBe(true);
    expect(result.capabilities.completionProvider).toBeTruthy();
  });

  it("publishes parse diagnostics with the error position", async () => {
    const server = await initialized();
    const { uri } = workspace("from orders\nselect\n");
    const diagnostics = await open(server, uri, "from orders\nselect\n");
    expect(diagnostics.diagnostics.length).toBe(1);
    expect(diagnostics.diagnostics[0].message).toMatch(/select requires at least one column/);
    expect(diagnostics.diagnostics[0].range.start.line).toBe(1);
  });

  it("publishes semantic diagnostics when a schema is available", async () => {
    const server = await initialized();
    const text = "# schema: schema.json\nfrom orders\nselect missing_column\n";
    const { uri } = workspace(text);
    const diagnostics = await open(server, uri, text);
    expect(diagnostics.diagnostics.length).toBe(1);
    expect(diagnostics.diagnostics[0].message).toMatch(/unknown column 'missing_column'/);
    expect(diagnostics.diagnostics[0].message).toMatch(/stage 2 \(select\)/);
    expect(diagnostics.diagnostics[0].range.start.line).toBe(2);
  });

  it("has no diagnostics for a valid program", async () => {
    const server = await initialized();
    const { uri } = workspace();
    const diagnostics = await open(server, uri, QUERY);
    expect(diagnostics.diagnostics).toEqual([]);
  });

  it("completes tables after from and columns inside a stage", async () => {
    const server = await initialized();
    const { uri } = workspace();
    await open(server, uri, QUERY);

    const tables = (await server.request("textDocument/completion", {
      textDocument: { uri },
      position: { line: 1, character: 5 },
    })) as any[];
    expect(tables.map((item) => item.label)).toContain("orders");

    const columns = (await server.request("textDocument/completion", {
      textDocument: { uri },
      position: { line: 3, character: 22 },
    })) as any[];
    expect(columns.map((item) => item.label)).toEqual(
      expect.arrayContaining(["order_id", "customer", "quantity"]),
    );
  });

  it("hovers a stage with its generated SQL", async () => {
    const server = await initialized();
    const { uri } = workspace();
    await open(server, uri, QUERY);

    const hover = (await server.request("textDocument/hover", {
      textDocument: { uri },
      position: { line: 3, character: 3 },
    })) as any;
    expect(hover.contents.value).toMatch(/stage 3 \(select\)/);
    expect(hover.contents.value).toMatch(/```sql/);
    expect(hover.contents.value).toMatch(/FROM stage_2/);
  });

  it("formats a non-canonical program", async () => {
    const server = await initialized();
    const text = "# schema: schema.json\nFROM orders\nfilter quantity>1\n";
    const { uri } = workspace(text);
    await open(server, uri, text);

    const edits = (await server.request("textDocument/formatting", {
      textDocument: { uri },
      options: { tabSize: 4, insertSpaces: true },
    })) as any[];
    expect(edits.length).toBe(1);
    expect(edits[0].newText).toMatch(/^# schema: schema\.json\nfrom orders\nfilter quantity > 1\n$/);
  });

  it("returns a document outline of stages", async () => {
    const server = await initialized();
    const { uri } = workspace();
    await open(server, uri, QUERY);

    const symbols = (await server.request("textDocument/documentSymbol", {
      textDocument: { uri },
    })) as any[];
    expect(symbols.map((symbol) => symbol.name)).toEqual([
      "1 from", "2 filter", "3 select",
    ]);
    expect(symbols[1].range.start.line).toBe(2);
  });
});
