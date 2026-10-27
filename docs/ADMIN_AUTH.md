# Single-administrator website login

The website uses one existing Apache `htpasswd` account. It does not create accounts, accept registration, or change the original password. Password verification runs the installed `htpasswd` executable with the password on standard input, never in command arguments. This supports existing APR1 records without implementing a password-hash algorithm in the application.

Unauthenticated responses, graph data, and search indexes must exclude personal ratings, personal rating explanations, and personal document contents. Login reveals private feedback through an authenticated endpoint; document pages and assets also require server authorization. Hiding an element with CSS is not an access control.

## Configuration

Keep these values in a private service environment file or local configuration outside Git. All paths below are examples.

```sh
ARCHIVIST_AUTH_HTPASSWD_FILE=/path/to/private/docs.htpasswd
ARCHIVIST_AUTH_HTPASSWD_BINARY=/usr/bin/htpasswd
ARCHIVIST_AUTH_DB=/path/to/private/auth-state/sessions.sqlite3
ARCHIVIST_AUTH_PRIVATE_DATA_FILE=/path/to/archive/site-private/admin-data.json
ARCHIVIST_AUTH_PUBLIC_URL=https://example.com
```

`ARCHIVIST_AUTH_USERNAME` can select the administrator from a password file. If omitted, the file must contain exactly one account. Invalid, missing, or ambiguous credentials disable login; local development has no authentication bypass. `ARCHIVIST_AUTH_ENABLED=false` disables authentication explicitly.

`ARCHIVIST_AUTH_ALLOWED_ORIGINS` optionally accepts a comma-separated list of exact origins. Otherwise the service reuses mailbox origins and its public URL. An Origin is a scheme, host, and optional port, with no path. Incoming `Host` headers never become trusted origins automatically.

The default session path is `archive/mailbox/admin-sessions.sqlite3`, and the default private export is `archive/site-private/admin-data.json`. Relative configured paths resolve against the project root. Keep every credential, session database, and private export outside the published site directory.

The session database requires a dedicated directory with permissions `0700`; the database is created with permissions `0600`. Give the service account read access to a private `0600` copy of the existing password file; copying its hash preserves the password. Changing that credential invalidates existing sessions. The service account also needs read access to the private export and write access to the session directory. Do not grant the web service permission to edit the credential file.

Run the combined small WSGI application with a production server:

```sh
gunicorn --bind 127.0.0.1:8092 'archivist.web.auth:create_site_api_app()'
```

The mailbox registration controls trusted proxy handling. Enable it only behind a private local listener and a reverse proxy that overwrites forwarded client address and scheme headers. Never trust client-supplied forwarding headers directly.

All authenticated API traffic must reach the same session store. With multiple static origins, route `/api/auth/` and `/api/admin/` to the designated API origin and bypass CDN caching. A private document origin can make an internal `POST /api/auth/check` request with the original Cookie header; do not expose any private document until that check returns `204`. Nginx `auth_request` subrequests need an explicit `proxy_method POST`, no request body, and a blank content length. A response of `401` must not trigger a Basic authentication challenge.

The builder writes the private export beside the archive, outside the static output. Before the first deployment, provision `site-private/` under each origin's configured archive directory with mode `0700`, owned by the deployment account. `archivist deploy` transfers the export with its `0600` permissions before publishing the static pages; `--skip-build` requires an existing export. Publish only the generated static directory. Never add a generic archive alias to Nginx.

Protect both the exact `/docs` path and the entire `/docs/` prefix, including HTML, images, HEAD, and Range requests. Use a `^~ /docs/` location so generic asset regular expressions cannot bypass authorization. All document responses, including redirects and errors, must use `Cache-Control: private, no-store` and `Vary: Cookie`. Remove the old Basic authentication gate when enabling session authentication; there must be no independent fallback that bypasses the session check. Unauthorized document requests should redirect to `/login/?next=...`, while API requests return `401`.

The Benchmark section is also administrator-only. Apply the same gate to the exact `/benchmark` path and the entire `/benchmark/` prefix, and hide its navigation link until a session is verified. Unauthorized Benchmark JSON requests return `401` with no data; other requests redirect to login. Remove or protect any existing exact `/benchmark/data.json` location, since it takes precedence over prefix locations. All Benchmark responses, including data and errors, need `private, no-store` and `Vary: Cookie`. The public graph export intentionally omits the legacy full Benchmark table; the graph's paper relationships and dataset labels remain public.

On the API origin, the internal authorization location can use this pattern:

```nginx
location = /_archivist_admin_check {
    internal;
    proxy_pass http://127.0.0.1:8092/api/auth/check;
    proxy_method POST;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header Cookie $http_cookie;
    access_log off;
}
```

Document locations use `auth_request /_archivist_admin_check;`. On a secondary static origin, the same internal location can instead proxy to the public HTTPS API endpoint, provided all POST requests route to the designated API origin. Set a fixed upstream Host, enable SNI and certificate verification with the system CA bundle and `proxy_ssl_verify_depth 3`, forward the Cookie, and set bounded connection/read timeouts. Verify the upstream certificate chain during setup; an insufficient verification depth must be repaired without disabling certificate checks. Never cache authorization responses or allow access after an upstream error. This preserves immediate logout across origins without sharing the session database.

The source archive can keep owner-only document images. The builder makes only the copies under the generated `docs/` tree readable by the static server, whose authorization gate protects them. Do not loosen the source archive, credential, session, or private-export permissions to repair an image error.

## API contract

All endpoints below accept `POST`. Browser requests use JSON objects and the configured Origin. Responses use `Cache-Control: private, no-store` and `Vary: Cookie`.

| Endpoint | Request | Response |
| --- | --- | --- |
| `/api/auth/session` | `{}` | `{enabled, authenticated}`; authenticated responses also contain `username` and `csrf_token` |
| `/api/auth/login` | `{username, password}` | Session status and a session Cookie after successful verification |
| `/api/auth/logout` | `{}` and `X-CSRF-Token` | Revokes the session and expires its Cookie |
| `/api/auth/check` | Cookie only; no Origin required | `204` for a valid session, otherwise `401`; no response data or state change |
| `/api/admin/data` | `{}` and Cookie | Private rating export after authentication |

The private export contains only this structure:

```json
{
  "papers": {
    "fictional-paper": {
      "rating": 7,
      "rating_reason": "A fictional private explanation."
    }
  },
  "doc_count": 2
}
```

The service loads one configured export path, validates its shape, and returns only the fields shown. Request data cannot select a file. This endpoint provides no file management or archive editing.

`current_admin()` returns an authenticated administrator dictionary or `None`; `require_admin` guards an existing Flask view. Register the routes in the local application with `register_auth(app)`. Private server-rendered views still need their own authorization gate and `private, no-store` responses.

## Session and abuse boundaries

The host-only `__Host-archivist_admin` Cookie is Secure, HttpOnly, SameSite=Lax, and valid for seven days. It requires HTTPS; do not weaken it for an HTTP development host. SQLite stores a SHA-256 digest of the random session token, not the token. Logging in rotates the presented session. Logout revokes it immediately, and password-file changes invalidate it. A maximum of 32 active sessions prevents unbounded session growth.

Five unsuccessful attempts from one client address reserve the attempt budget for 15 minutes. Reservations are transactional, so concurrent requests cannot bypass the limit. Successful login clears that client's counter. The store retains keyed HMAC address digests rather than raw IP addresses, expires counters, and bounds the number of clients. This is a basic abuse control; it cannot stop an attacker who rotates addresses.

The original password hash remains protected by filesystem permissions. APR1 is retained for compatibility with the existing credential; it is an older hash format. To adopt a stronger password hash later, change the existing account deliberately with the administrator rather than silently altering credentials during deployment.

## Offline validation

```sh
uv run python -m unittest discover -s tests -p 'test_admin_auth.py' -v
uv run python -m compileall -q src scripts
```

Tests use fictional credentials and temporary databases. Apache `htpasswd` must be installed to exercise real password verification. No production inbox, archive, or credentials are used.
