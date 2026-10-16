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
- GitHub release `v0.1.0` is the single release (earlier drafts were collapsed
  into it), with the full artifact set for every component:
  `unsequel-0.1.0.tgz` (npm compiler), `unsequel-0.1.0-py3-none-any.whl`
  (Python package), and `unsequel-vscode-0.1.0.vsix` (VS Code extension, the
  language server bundled inside it). On publish, the two packaging workflows
  attach the four PyInstaller executables (DuckDB bundled), the four Node-free
  run bundles, and the four TypeScript compile executables, each smoke-tested
  first. All package versions are 0.1.0.
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
  `unsequel@0.1.0` cleanly (auth still required for the real upload), the
  Python wheel builds as `unsequel-0.1.0-py3-none-any.whl`, and the VSIX
  builds as `unsequel-vscode-0.1.0.vsix`. Owner commands after login:
  `npm login` then `npm publish` in `compiler/`; `python -m pip install build
  twine` then `python -m twine upload dist/*` for PyPI; `npx @vscode/vsce
  publish` for the Marketplace (choose the editor after feedback).
- Second SQL target decision (October 2026): deferred until external feedback
  confirms warehouse portability matters; DuckDB stays the locked first
  target (see `compiler/docs/backlog.md` P1).
