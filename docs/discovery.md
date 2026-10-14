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

- GitHub release `v0.6.0` is the initial package release with a Python wheel and
  `unsequel-plq 0.2.0` tarball (the short-lived npm name).
- The naming follow-up release is being prepared as `v0.7.0`: Python package
  `unsequel 0.7.0`, npm package/CLI `unsequel 0.3.0`, and the VS Code extension
  branded `UNSeQueL` (`0.3.0`). The Python prototype console script is
  `unsequel-python` so it does not collide with the production Node command.
- npm `usql`, `unsql`, and `plq` belong to other publishers. npm `unsequel` has
  an unpublished 2015 registry record; the owner must confirm the full name can
  be reclaimed when publishing.
  PyPI `unsequel` had no published release when checked.
- Attach the updated Python wheel, npm tarball, and VS Code VSIX to the
  follow-up GitHub release after final CI.
- PyPI/npm and VS Code Marketplace publication need owner-controlled
  accounts/tokens; see [CONTRIBUTING.md](../CONTRIBUTING.md) and the package
  metadata.
