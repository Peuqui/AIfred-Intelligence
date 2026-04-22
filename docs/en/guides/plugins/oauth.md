# OAuth Broker

**Files:** `aifred/lib/oauth/broker.py`, `aifred/lib/oauth/google.py`

Generic OAuth 2.0 broker for all Google plugins and future providers. Handles token storage (Fernet-encrypted), CSRF protection via state parameter, and automatic token refresh 60 seconds before expiry.

## Architecture

| File | Purpose |
|------|---------|
| `aifred/lib/oauth/broker.py` | Generic broker: provider registry, token storage, auto-refresh |
| `aifred/lib/oauth/google.py` | Google OAuth2 provider (Calendar, Contacts, Drive, Tasks) |
| `data/oauth_tokens.json` | Encrypted token file (one entry per provider) |
| `data/oauth_encryption_key.bin` | Fernet key — auto-generated on first use, permissions 0o600 |

**Token security:** Each provider's token is individually Fernet-encrypted. The JSON file reveals the structure (provider names as keys), but all values are unreadable without the key.

**CSRF protection:** State tokens with a 10-minute TTL. Expired or unknown state tokens are rejected at the callback.

**Auto-refresh:** `get_token()` checks the expiry timestamp and silently renews the token if it expires within 60 seconds.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/oauth/{provider}/auth-url` | GET | Generate auth URL. Query params: `redirect_uri`, `scopes` (comma-separated) |
| `/api/oauth/{provider}/callback` | GET | OAuth callback — called automatically by Google after login |
| `/api/oauth/{provider}/status` | GET | Check connection status (`connected: true/false`) |
| `/api/oauth/{provider}/` | DELETE | Delete tokens / disconnect |

`{provider}` is currently always `google`.

### Example: Generate auth URL

```bash
curl "http://localhost:8002/api/oauth/google/auth-url?redirect_uri=https://narnia.spdns.de:8443/api/oauth/google/callback&scopes=https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/contacts"
```

### Example: Check status

```bash
curl http://localhost:8002/api/oauth/google/status
# {"provider": "google", "connected": true}
```

### Example: Disconnect

```bash
curl -X DELETE http://localhost:8002/api/oauth/google/
```

## Available Google Scopes

| Constant in `google.py` | Scope URL | Description |
|-------------------------|-----------|-------------|
| `SCOPES_CALENDAR` | `auth/calendar` | Read and write calendar |
| `SCOPES_CONTACTS` | `auth/contacts` | Read and write contacts |
| `SCOPES_DRIVE_READONLY` | `auth/drive.readonly` | Read-only Drive access |
| `SCOPES_TASKS` | `auth/tasks` | Google Tasks |
| `SCOPES_PROFILE` | `openid`, `userinfo.email`, `userinfo.profile` | User profile |

**Note:** Google issues a refresh token only once. All required scopes must be requested in the initial auth flow. Adding scopes later requires a new OAuth flow.

## Adding a New Provider

1. Create a new file, e.g. `aifred/lib/oauth/myprovider.py`
2. Subclass `OAuthProvider` and implement three methods:
   ```python
   class MyProvider(OAuthProvider):
       name = "myprovider"

       def get_auth_url(self, scopes: list[str], redirect_uri: str, state: str) -> str: ...
       async def exchange_code(self, code: str, redirect_uri: str) -> TokenSet: ...
       async def refresh(self, token_set: TokenSet) -> TokenSet: ...
   ```
3. Register the provider at startup (e.g. in `aifred/app_startup.py`):
   ```python
   from aifred.lib.oauth.myprovider import MyProvider
   from aifred.lib.oauth.broker import oauth_broker
   oauth_broker.register(MyProvider())
   ```
4. The broker automatically exposes API endpoints at `/api/oauth/myprovider/...`
