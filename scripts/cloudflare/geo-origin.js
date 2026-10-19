/*
 * Cloudflare module Worker draft. Deploy with compatibility_date >= 2024-11-11.
 * Set PUBLIC_HOST to the zone apex, and ALIYUN_ORIGIN to a same-zone DNS-only
 * A record pointing at the Aliyun origin. Keep apex/www DNS pointed at the primary origin.
 * Both origins must validate HTTPS for PUBLIC_HOST and www.PUBLIC_HOST.
 * Bind routes only to PUBLIC_HOST/* and www.PUBLIC_HOST/*, fail-open.
 */
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const publicHost = env.PUBLIC_HOST;
    if (!publicHost || ![publicHost, `www.${publicHost}`].includes(url.hostname)) {
      return new Response("Not Found", { status: 404 });
    }

    const replaySafe = request.method === "GET" || request.method === "HEAD";
    // Origins enforce all access control; an unexpected Worker error may safely
    // leave a read-only request on the primary origin.
    if (replaySafe) ctx.passThroughOnException();

    const bypassCache = url.pathname === "/docs" ||
      url.pathname.startsWith("/docs/") ||
      /^\/reading\/\d+\/[^/]+\/pdf\/?$/.test(url.pathname) ||
      request.headers.has("Authorization");

    const baseOptions = { redirect: "manual" };
    if (bypassCache) baseOptions.cache = "no-store";

    async function defaultOrigin() {
      try {
        // The original URL is intentionally unchanged: keep Host, SNI, the
        // ordinary cache key, redirects, Range and conditional request headers.
        return await fetch(request, baseOptions);
      } catch {
        return new Response("Bad Gateway", {
          status: 502,
          headers: { "Cache-Control": "no-store" },
        });
      }
    }

    // Keep HTTP-01 validation on the primary origin that owns certificate renewal.
    const primaryOnly = url.pathname.startsWith("/.well-known/acme-challenge/");
    if (!replaySafe || primaryOnly || request.cf?.country !== "CN" || !env.ALIYUN_ORIGIN) {
      return defaultOrigin();
    }

    const timeout = new AbortController();
    const timer = setTimeout(() => timeout.abort(), 8000);
    let response;
    try {
      response = await fetch(request, {
        ...baseOptions,
        signal: timeout.signal,
        cf: { resolveOverride: env.ALIYUN_ORIGIN },
      });
    } catch {
      // Network/TLS/timeout failures may retry the bodyless request on the primary origin.
      return defaultOrigin();
    } finally {
      clearTimeout(timer);
    }

    // Do not hide an auth denial, a missing file, or retired PDF status by
    // retrying another origin. Only server failures fall back to the primary origin.
    if (response.status >= 500 && response.status <= 599) {
      if (response.body) {
        try { await response.body.cancel(); } catch { /* release best effort */ }
      }
      // Avoid reading a cached failing response for the same URL on retry.
      try {
        return await fetch(request, { ...baseOptions, cache: "no-store" });
      } catch {
        return new Response("Bad Gateway", {
          status: 502,
          headers: { "Cache-Control": "no-store" },
        });
      }
    }
    return response;
  },
};
