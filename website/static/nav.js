/* Moving between pages without reloading them, so the star background keeps
   drifting and nothing flashes: a click on a link to another page of this
   site fetches that page and swaps in its header, content and footer. Any
   problem (offline, an unusual link) falls back to a normal page load. */
(function () {
  if (!window.fetch || !window.DOMParser || !history.pushState) return;
  var cache = {};

  function sameSite(a) {
    if (!a || a.target && a.target !== "_self" || a.hasAttribute("download")) return false;
    var url = new URL(a.href, location.href);
    if (url.origin !== location.origin) return false;
    var root = document.documentElement.getAttribute("data-root") || "/";
    if (url.pathname.indexOf(root) !== 0) return false;
    return /\/$|\.html$/.test(url.pathname);            // pages, not files like sitemap.xml
  }

  function load(href) {
    if (!cache[href]) {
      cache[href] = fetch(href, { credentials: "same-origin" }).then(function (r) {
        if (!r.ok || (r.headers.get("content-type") || "").indexOf("text/html") < 0) throw new Error(r.status);
        return r.text();
      });
      cache[href].catch(function () { delete cache[href]; });
    }
    return cache[href];
  }

  function swapHead(doc) {
    document.title = doc.title;
    ['meta[name="description"]', 'link[rel="canonical"]', 'meta[name="robots"]', 'meta[property^="og:"]',
     'script[type="application/ld+json"]'].forEach(function (sel) {
      document.querySelectorAll("head " + sel).forEach(function (el) { el.remove(); });
      doc.querySelectorAll("head " + sel).forEach(function (el) { document.head.appendChild(document.importNode(el, true)); });
    });
  }

  function show(href, html, push) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var parts = ["header.site-header", "main", "footer.site-footer"];
    if (!parts.every(function (s) { return doc.querySelector(s) && document.querySelector(s); })) {
      location.href = href;
      return;
    }
    swapHead(doc);
    parts.forEach(function (s) { document.querySelector(s).replaceWith(document.importNode(doc.querySelector(s), true)); });
    document.body.className = doc.body.className;
    if (push) history.pushState({}, "", href);
    var hash = new URL(href, location.href).hash;
    var target = hash && document.getElementById(decodeURIComponent(hash.slice(1)));
    if (target) target.scrollIntoView(); else if (push) window.scrollTo(0, 0);
    var main = document.querySelector("main");
    if (main) { main.setAttribute("tabindex", "-1"); main.focus({ preventScroll: true }); }
  }

  function go(href, push) {
    load(href).then(function (html) { show(href, html, push); }, function () { location.href = href; });
  }

  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest && e.target.closest("a[href]");
    if (!sameSite(a)) return;
    var url = new URL(a.href, location.href);
    if (url.pathname === location.pathname && url.search === location.search) return;   // same page: let # links work
    e.preventDefault();
    go(url.href, true);
  });

  // Start fetching when the pointer rests on a link, so the click feels instant
  document.addEventListener("mouseover", function (e) {
    var a = e.target.closest && e.target.closest("a[href]");
    if (sameSite(a)) load(new URL(a.href, location.href).href);
  });

  window.addEventListener("popstate", function () { go(location.href, false); });
})();
