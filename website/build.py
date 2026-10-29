"""Builds the PathRenamer website into website/_site: plain HTML and CSS, no
dependencies.

    python website/build.py

Pages are HTML fragments in website/pages/ that start with a JSON block
(<!--meta {...} -->) for their title, description, address and structured
data. Values like {{ version }} come from site.json, src/main.py and the
build. The build fails on a link to a page or file that doesn't exist.
"""
import html
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "_site"

NAV = (("/", "Home"), ("/download/", "Download"), ("/pricing/", "Pricing"),
       ("/guides/", "Guides"), ("/faq/", "FAQ"))


def app_version():
    text = (ROOT / "src" / "version.py").read_text(encoding="utf-8")
    m = re.search(r'^APP_VERSION = "(.+)"', text, re.M)
    return m.group(1) if m else ""


def last_changed(path):
    """Date of the last commit touching path (today outside git)."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%cs", "--", str(path)], cwd=ROOT,
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return out or date.today().isoformat()
    except (OSError, subprocess.SubprocessError):
        return date.today().isoformat()


def markdown(text):
    """The little Markdown RELEASE_NOTES.md uses: paragraphs, - lists, **bold**,
    `code` and [links](https://...)."""
    out, items = [], []

    def inline(s):
        s = html.escape(s, quote=False)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s\"<>]+)\)", r'<a href="\2">\1</a>', s)
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)

    def flush():
        if items:
            out.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
            items.clear()
    for block in text.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("- "):
                items.append(inline(line[2:]))
            elif items and line.startswith("  "):
                items[-1] += " " + inline(line.strip())
            else:
                flush()
                out.append(f"<p>{inline(line)}</p>")
        flush()
    return "\n".join(out)


def fill(text, values, where):
    def sub(m):
        key = m.group(1)
        if key not in values:
            sys.exit(f"{where}: unknown {{{{ {key} }}}}")
        return str(values[key])
    return re.sub(r"\{\{\s*([a-z_]+)\s*\}\}", sub, text)


def read_page(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"<!--meta\s*(\{.*?\})\s*-->\s*", text, re.S)
    if not m:
        sys.exit(f"{path}: starts without a <!--meta {{...}} --> block")
    return json.loads(m.group(1)), text[m.end():]


def json_ld(data):
    return ('<script type="application/ld+json">' +
            json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") +
            "</script>")


def structured_data(meta, site, values, faq, url):
    blocks = []
    kinds = meta.get("schema", [])
    if "software" in kinds:
        blocks.append({
            "@context": "https://schema.org", "@type": "SoftwareApplication",
            "name": "PathRenamer", "operatingSystem": "Windows 10, Windows 11",
            "applicationCategory": "MultimediaApplication",
            "description": site["description"], "url": site["base_url"],
            "downloadUrl": site["download_url"], "softwareVersion": values["version"],
            "screenshot": site["base_url"] + "img/app-dark.webp",
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
        })
    if "website" in kinds:
        blocks.append({"@context": "https://schema.org", "@type": "WebSite", "name": "PathRenamer",
                       "url": site["base_url"]})
    if "faq" in kinds:
        blocks.append({
            "@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": q["q"],
                            "acceptedAnswer": {"@type": "Answer", "text": re.sub(r"<[^>]+>", "", fill(q["a"], values, "faq"))}}
                           for q in faq],
        })
    if "article" in kinds:
        blocks.append({
            "@context": "https://schema.org", "@type": "TechArticle", "headline": meta["h1"],
            "description": meta["description"], "url": url, "dateModified": meta["_modified"],
            "image": site["base_url"] + "img/og-image.png",
            "author": {"@type": "Organization", "name": "PathRenamer", "url": site["base_url"]},
        })
    if meta.get("crumbs"):
        items = [{"@type": "ListItem", "position": i + 1, "name": name,
                  "item": site["base_url"] + path.lstrip("/")}
                 for i, (path, name) in enumerate(meta["crumbs"])]
        blocks.append({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items})
    return "\n".join(json_ld(b) for b in blocks)


def faq_html(faq, values):
    parts = []
    for q in faq:
        parts.append(f'<details class="faq"><summary>{html.escape(q["q"])}</summary>'
                     f'<div>{fill(q["a"], values, "faq")}</div></details>')
    return "\n".join(parts)


def pro_cta(site, root):
    """The Pro box on the pricing page: buy, or free during early access."""
    if site.get("checkout_url"):
        return (f'<p class="price">{html.escape(site["pro_price"])}</p>'
                f'<p class="price-note">{html.escape(site["pro_price_note"])}</p>'
                f'<a class="button primary" href="{html.escape(site["checkout_url"])}">Get Pro</a>'
                f'<p class="small">Try it free for {site["trial_days"]} days in the app first.</p>')
    return ('<p class="price">Free</p><p class="price-note">during early access</p>'
            f'<a class="button primary" href="{root}download/">Download and try it</a>'
            '<p class="small">Every Pro feature is unlocked in the app for now.</p>')


def check_links(out_dir, root_path):
    """Every internal href/src must point at a file of the built site."""
    broken = []
    for page in out_dir.rglob("*.html"):
        for link in re.findall(r'(?:href|src)="([^"#?]+)', page.read_text(encoding="utf-8")):
            if re.match(r"^[a-z]+:", link) or link.startswith("//"):
                continue
            if not link.startswith(root_path):
                broken.append(f"{page.relative_to(out_dir)}: {link} (not under {root_path})")
                continue
            target = out_dir / link[len(root_path):]
            if link.endswith("/"):
                target = target / "index.html"
            if not target.exists():
                broken.append(f"{page.relative_to(out_dir)}: {link}")
    return broken


def nav_links(root, current):
    links = []
    for path, name in NAV:
        here = ' aria-current="page"' if path == current else ""
        links.append(f'<a href="{root}{path.lstrip("/")}"{here}>{name}</a>')
    return "".join(links)


def build():
    site = json.loads((HERE / "site.json").read_text(encoding="utf-8"))
    faq = json.loads((HERE / "faq.json").read_text(encoding="utf-8"))
    base = site["base_url"]
    root = urlparse(base).path or "/"
    version = app_version()
    values = dict(site, root=root, version=version, year=date.today().year,
                  release_notes=markdown((ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")))
    values["pro_cta"] = pro_cta(site, root)
    # One sentence about Pro that matches the pricing page: on sale, or early access
    values["pro_now"] = (f"The automatic Pro features come with a free {site['trial_days']}-day trial."
                         if site.get("checkout_url") else
                         "The automatic Pro features are unlocked during early access.")
    values["faq_items"] = faq_html(faq, values)
    values["faq_top"] = faq_html(faq[:4], values)
    layout = (HERE / "templates" / "layout.html").read_text(encoding="utf-8")

    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(HERE / "static", OUT)
    sitemap = []
    for src in sorted((HERE / "pages").rglob("*.html")):
        meta, body = read_page(src)
        meta["_modified"] = last_changed(src)
        path = meta["path"]
        url = base + path.lstrip("/")
        nav = nav_links(root, meta.get("nav", path))
        title = fill(meta["title"], values, src.name)
        description = fill(meta["description"], values, src.name)
        meta["h1"] = fill(meta.get("h1", meta["title"]), values, src.name)
        meta["description"] = description
        page_values = dict(values, title=html.escape(title), description=html.escape(description),
                           canonical=url, nav=nav, page_class=meta.get("class", "page"),
                           structured_data=structured_data(meta, site, values, faq, url),
                           modified=meta["_modified"],
                           robots='<meta name="robots" content="noindex">' if meta.get("noindex") else "")
        page_values["body"] = fill(body, page_values, src.name)
        result = fill(layout, page_values, "layout.html")
        target = OUT / ("404.html" if path == "/404.html" else Path(path.strip("/")) / "index.html")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result, encoding="utf-8")
        if not meta.get("noindex"):
            sitemap.append((url, meta["_modified"], meta.get("priority", 0.5)))

    (OUT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
        "".join(f"  <url><loc>{html.escape(u)}</loc><lastmod>{d}</lastmod><priority>{p}</priority></url>\n"
                for u, d, p in sitemap) + "</urlset>\n", encoding="utf-8")
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {base}sitemap.xml\n", encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    if site.get("custom_domain"):
        (OUT / "CNAME").write_text(site["custom_domain"] + "\n", encoding="utf-8")

    broken = check_links(OUT, root)
    if broken:
        sys.exit("Broken links:\n  " + "\n  ".join(broken))
    print(f"Built {len(sitemap) + 1} pages into {OUT.relative_to(ROOT)} (PathRenamer {version})")


if __name__ == "__main__":
    build()
