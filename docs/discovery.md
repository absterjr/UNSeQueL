# Website, search, and release checklist

The repo About description and topics are set. Search Console verification,
personal profile links, external posts, social-preview upload, and package
publication require the owner's accounts and should be completed from the
owner's setup.

## GitHub Pages

- Site source: [`site/`](../site/)
- Deploy workflow: [`.github/workflows/pages.yml`](../.github/workflows/pages.yml)
- Canonical URL (deployed): <https://absterjr.github.io/UNSeQueL/>
- Documentation pages: <https://absterjr.github.io/UNSeQueL/docs/> (getting
  started, language, expressions, CLI, semantics, examples, VS Code,
  prototype, comparison, roadmap)
- Sitemap: <https://absterjr.github.io/UNSeQueL/sitemap.xml>
- Robots file: <https://absterjr.github.io/UNSeQueL/robots.txt>

Pages is enabled with **Build and deployment → Source: GitHub Actions**. The
Pages workflow is green and the public URL, sitemap, and robots file have been
checked.

## Google Search Console and Bing Webmaster Tools

The site needs to be live before verification.

1. Add the URL-prefix property `https://absterjr.github.io/UNSeQueL/` in Google
   Search Console.
2. Choose **HTML tag** verification. Copy the exact token Google supplies into
   the `google-site-verification` meta tag in `site/index.html`, push, wait for
   Pages to deploy, then click **Verify**. Alternatively, use Google's HTML
   file method and add the exact supplied file under `site/`.
3. Submit `https://absterjr.github.io/UNSeQueL/sitemap.xml` in Search Console.
4. Add the same URL-prefix property in Bing Webmaster Tools; import the verified
   Google property or use Bing's HTML-tag/file method, then submit the sitemap.

Verification values are account-specific, so this repo intentionally does not
contain placeholder tokens.

## Repository metadata and social preview

- About description (set): “UNSeQueL is a FROM-first query language for
  writing SQL-like transformations in execution order; Python prototype and
  DuckDB/TypeScript compiler.”
- Topics set: `sql`, `query-language`, `duckdb`, `sql-pipeline`,
  `pipe-syntax`, `sql-alternative`, `from-first`, `typescript`, `python`,
  `data-analysis`.
- Social preview source/image: `site/social-preview.svg` and
  `site/social-preview.png`. Upload the PNG under **Settings → General → Social
  preview**; the repository API does not expose this upload field.
- Use `UNSeQueL` consistently in page titles and post headlines. Keep
  problem-oriented phrases such as “write SQL in execution order” and
  “FROM-first query language” in the page title, description, and article text.

## Inbound links to pursue

- Add a short project link to the owner's GitHub profile README and personal
  site.
- Profile README starter: “Building UNSeQueL — a FROM-first query language for
  writing SQL-like transformations in execution order.” Link the project site
  and repository.
- Publish the reviewed draft at
  [`articles/sql-in-execution-order.md`](articles/sql-in-execution-order.md)
  on dev.to, Hashnode, or the personal site; link the repo and this landing page.
- Review contribution rules and submit a concise, relevant entry to
  `awesome-sql`, `awesome-query-languages`, and appropriate PRQL-adjacent lists.
  Do not submit duplicate promotional entries where the list is not a fit.

## Releases and packages

- Standalone binaries: the `Standalone binaries` workflow builds
  `unsequel-windows-x64.exe`, `unsequel-linux-x64`, `unsequel-macos-x64`, and
  `unsequel-macos-arm64` with PyInstaller (`packaging/unsequel.spec`) and
  attaches them to every newly published release automatically
  (`gh workflow run binaries.yml` re-runs it manually). The executables
  bundle DuckDB and need no installed toolchain; they are unsigned, so
  Windows SmartScreen may warn until a signing certificate is configured.
- GitHub release `v0.1.0` was the first release, with the full artifact set
  for every component: `unsequel-0.1.0.tgz` (npm compiler),
  `unsequel-0.1.0-py3-none-any.whl` (Python prototype), and
  `unsequel-vscode-0.1.0.vsix` (VS Code extension); all package versions
  started at 0.1.0, and the four standalone executables were backfilled onto
  it.
- GitHub release `v0.6.1` fixes the follow-ups from the 0.6.0 review: number
  literals now reach DuckDB as source text (no rounding of large IDs; edge
  fixtures with decoy rows), schema snapshots list persistent tables only,
  read-query runtime errors name the failing stage, the VSIX bundles the
  language server (no 63 MB download; run bundles are found in
  `~/unsequel-run` or extension storage, never the workspace), `# source`
  shadowing prints a note, `sql` hatches that try to `CREATE TABLE` get a
  pointed hint, and JSON columns are typed once (no mixed number/string);
  assets: `unsequel-0.6.1.tgz`, `unsequel-0.6.1-py3-none-any.whl`,
  `unsequel-vscode-0.6.1.vsix`, plus the platform binaries from both
  workflows.
- GitHub release `v0.6.0` adds grammar v1.5 (scientific-notation literals,
  `CAST` type parameters), one number-output rule across tracks with
  `examples/edge/` fixtures (CSV/JSON `--out` goes through the stdout
  formatter; parity 28/28 with DuckDB, 26/26 with the memory engine, both in
  CI), `check` parity (TypeScript checks columns when it can load sources;
  Python surfaces parse errors first), stage-labeled statement errors with a
  snapshot refresh after writes, the DuckDB-backed whole-file CSV sniffer on
  the Python side (CI memory ceiling), and VS Code run-bundle discovery;
  assets: `unsequel-0.6.0.tgz`, `unsequel-0.6.0-py3-none-any.whl`,
  `unsequel-vscode-0.6.0.vsix`, plus the platform binaries from both
  workflows.
- GitHub release `v0.5.0` adds `create or replace`, `into` BY NAME with type
  checks, transactional programs, `--create-db`, `import --replace`, the
  whole-file CSV sniffer (both tracks) with wide-integer correction, one
  shared output format, and `scripts/parity.sh` (27/27 identical across
  tracks; wired into CI); assets: `unsequel-0.5.0.tgz`,
  `unsequel-0.5.0-py3-none-any.whl`, `unsequel-vscode-0.5.0.vsix`, plus the
  platform binaries from both workflows.
- GitHub release `v0.4.0` added grammar v1.4 write statements (`create
  table|view = (pipeline)`, `into`), `unsequel import`, and LSP semantic
  tokens; assets: `unsequel-0.4.0.tgz`, `unsequel-0.4.0-py3-none-any.whl`,
  `unsequel-vscode-0.4.0.vsix`, plus the platform binaries from both
  workflows.
- GitHub release `v0.3.0` shipped the language server extension, the SEA run
  bundle (`unsequel-run-<platform>.tar.gz`, DuckDB in `modules/`, no Node
  needed), the PyInstaller executables with DuckDB as the default engine,
  `--out` sinks, file-backed databases (`--db`, `--attach`, `# db:`), and the
  `schema` snapshot command the LSP reads; assets: `unsequel-0.3.0.tgz`,
  `unsequel-0.3.0-py3-none-any.whl`, `unsequel-vscode-0.3.0.vsix`, plus the
  platform binaries from both workflows.
- GitHub release `v0.2.0` added the Python port of the canonical language
  (front-end, analyzer, DuckDB codegen, CLI wiring), the standalone packaging,
  and the legacy-surface retirement notices; assets: `unsequel-0.2.0.tgz`,
  `unsequel-0.2.0-py3-none-any.whl`, `unsequel-vscode-0.2.0.vsix`, plus the
  platform binaries attached by the `Standalone binaries` workflow.
- The npm names `plq`, `usql`, and `unsql` belong to other publishers;
  `unsequel` is the single name now used for the Python package, npm
  package/CLI, and VS Code extension (`.unsequel` query files).
- npm `unsequel` has an unpublished 2015 registry record; the owner must
  confirm the name can be reclaimed when publishing. PyPI had no `unsequel`
  release when checked.
- PyPI/npm and VS Code Marketplace publication need owner-controlled
  accounts/tokens; see [CONTRIBUTING.md](../CONTRIBUTING.md) and the package
  metadata.
- Publishing prep verified from this machine: `npm publish --dry-run` packs
  `unsequel@0.6.1` cleanly (auth still required for the real upload), the
  Python wheel builds as `unsequel-0.6.1-py3-none-any.whl`, and the VSIX
  builds as `unsequel-vscode-0.6.1.vsix`. Owner commands after login:
  `npm login` then `npm publish` in `compiler/`; `python -m pip install build
  twine` then `python -m twine upload dist/*` for PyPI; `npx @vscode/vsce
  publish` for the Marketplace (choose the editor after feedback).
- Second SQL target decision (October 2026): deferred until external feedback
  confirms warehouse portability matters; DuckDB stays the locked first
  target (see `compiler/docs/backlog.md` P1).
