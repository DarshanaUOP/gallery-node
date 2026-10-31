# Application Security Review

**Review date:** 2026-10-31  
**Scope:** Standard security review of the application, with the backend dashboard entry point and related API, frontend, and server configuration surfaces examined.  
**Status:** All three reported issues have been remediated and verified with focused checks.

## Summary

The review initially identified three high-severity issues. The API was reachable over the network without authentication, accepted database records containing caller-controlled filesystem paths that could be used to serve local files, and rendered attacker-controlled album identifiers in inline event handlers. Remediations are now in place. The server is loopback-only; access from other devices on the network is intentionally no longer supported by the default configuration.

| # | Severity | File | Lines | Vulnerability | Confidence |
|---|----------|------|-------|---------------|------------|
| 1 | 🟠 HIGH | [app.py](app/src/backend/app.py), [tray.py](app/src/backend/tray.py) | 2377, 3813, 3821; 107, 113 | Unauthenticated network-accessible API with broad CORS | 9/10 |
| 2 | 🟠 HIGH | [app.py](app/src/backend/app.py) | 2618, 2672, 3651 | Caller-controlled paths can expose local files through the image endpoint | 9/10 |
| 3 | 🟠 HIGH | [app.js](app/src/frontend/static/app.js), [app.py](app/src/backend/app.py) | 402-427; 3658-3665 | Stored album IDs are interpolated into inline event-handler HTML | 9/10 |

## Findings

### 1. Unauthenticated API is exposed on all network interfaces

**Severity:** HIGH  
**Confidence:** 9/10  
**Locations:** [app.py](app/src/backend/app.py#L2377), [app.py](app/src/backend/app.py#L3813), [app.py](app/src/backend/app.py#L3821), [tray.py](app/src/backend/tray.py#L107)

The server binds to `0.0.0.0`, making it reachable from other devices on accessible networks. The API has no authentication or authorization, and CORS is broadly enabled.

**Impact:** A network-reachable attacker may access indexed media, GPS metadata, and filesystem paths, and invoke endpoints that change configuration, locations, or library data.

**Recommended remediation:** Bind to loopback by default. If remote access is a supported feature, require authentication and authorization, and restrict allowed origins to explicitly trusted origins. CORS restrictions alone are not a substitute for authentication.

**Remediation applied:** Both server launch paths now bind to `127.0.0.1`; broad CORS support and its dependency were removed. The backend rejects non-loopback Host headers and cross-origin state-changing requests.

### 2. Media endpoint can serve files outside configured media roots

**Severity:** HIGH  
**Confidence:** 9/10  
**Locations:** [app.py](app/src/backend/app.py#L2618), [app.py](app/src/backend/app.py#L2672), [app.py](app/src/backend/app.py#L3651)

The `/api/db` endpoint accepts media records containing caller-controlled paths and names. `_resolve_path` joins those values and checks that the result is a file, but does not ensure the resolved path is within configured media roots. The `/api/image/<unique_name>` endpoint then serves the referenced file.

**Impact:** A caller able to reach the unauthenticated API can submit a record referencing a readable local file and retrieve that file through the image endpoint.

**Recommended remediation:** Do not treat client-supplied filesystem paths as trusted records. Resolve and canonicalize paths, then enforce that each resolved path is contained within an explicitly configured media root before storing or serving it. Apply the same check at the point of serving.

**Remediation applied:** `/api/db` rejects client-supplied media-record updates. The image-serving resolver canonicalizes paths and only serves files contained in a currently configured media root, preventing path traversal and symlink escapes.

### 3. Stored album IDs can inject executable markup

**Severity:** HIGH  
**Confidence:** 9/10  
**Locations:** [app.js](app/src/frontend/static/app.js#L402), [app.py](app/src/backend/app.py#L3658)

Album IDs from the database are interpolated into HTML inline event handlers without escaping. The `/api/db` POST endpoint accepts and persists caller-supplied album IDs, allowing a crafted value to break out of the attribute and inject markup or script.

**Impact:** When the affected album is rendered, injected script can execute in the gallery origin and access or modify data available to the page.

**Recommended remediation:** Avoid inline event handlers and construct elements using DOM APIs. Bind event listeners directly, and treat persisted IDs as untrusted data; validate their expected format and use safe text/attribute APIs rather than HTML string interpolation.

**Remediation applied:** Album navigation now creates DOM nodes and attaches event listeners without interpolating IDs into HTML. The API also validates persisted album IDs against a bounded safe-character set.

## Verification

- Seven focused Flask test-client checks passed for non-local Host rejection, cross-origin mutation rejection, unsafe album-ID rejection, media-record write rejection, valid album saves, allowed media serving within a configured root, and blocked serving outside that root.
- Python syntax parsing and `node --check` passed.
- `git diff --check` passed with CRLF-aware whitespace handling for the existing CRLF file.

## Limitations

This report records the findings identified in the standard review; it is not a guarantee that the application is free of other security issues. The focused checks cover the reported remediations, not every route or deployment configuration.
