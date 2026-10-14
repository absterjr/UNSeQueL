# Website, search, and release checklist

The repo About description and topics are set. Search Console verification,
personal profile links, external posts, social-preview upload, and package
publication require the owner's accounts and should be completed from the
owner's setup.

## GitHub Pages

- Site source: [`site/`](../site/)
- Deploy workflow: [`.github/workflows/pages.yml`](../.github/workflows/pages.yml)
- Canonical URL (deployed): <https://absterjr.github.io/UNSeQueL/>
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
- Suggested topics: `sql`, `query-language`, `duckdb`, `sql-pipeline`,
  `from-first`, `typescript`, `python`, `data-analysis`.
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

- GitHub release `v0.6.0` is live with a Python wheel, `unsequel-plq 0.2.0`
  npm tarball, and PLQ VS Code `0.2.0` VSIX attached.
- PyPI `unsequel` had no published release when checked. npm's short name
  `plq` is occupied by an unrelated `0.0.1` package; the planned compiler
  package name is `unsequel-plq` (the binary remains `plq`).
- Before tagging, finish the release notes, full CI, clean-install smoke tests,
  and `npm pack --dry-run` / Python wheel checks.
- PyPI and npm publication need owner-controlled accounts/tokens; see
  [CONTRIBUTING.md](../CONTRIBUTING.md) and the package metadata.
- The VS Code extension can be published separately after choosing its
  Marketplace publisher account.
