# OAuth Broker

**Dateien:** `aifred/lib/oauth/broker.py`, `aifred/lib/oauth/google.py`

Generischer OAuth 2.0 Broker fuer alle Google-Plugins und kuenftige Provider. Verwaltet Token-Speicherung (Fernet-verschluesselt), CSRF-Schutz ueber State-Parameter und automatischen Token-Refresh 60 Sekunden vor Ablauf.

## Architektur

| Datei | Aufgabe |
|-------|---------|
| `aifred/lib/oauth/broker.py` | Generischer Broker: Provider-Registry, Token-Storage, Auto-Refresh |
| `aifred/lib/oauth/google.py` | Google OAuth2 Provider (Calendar, Contacts, Drive, Tasks) |
| `data/oauth_tokens.json` | Verschluesselte Token-Datei (eine Eintrag pro Provider) |
| `data/oauth_encryption_key.bin` | Fernet-Key — auto-generiert beim ersten Start, Rechte 0o600 |

**Token-Sicherheit:** Jeder Provider-Token wird individuell Fernet-verschluesselt. Die JSON-Datei zeigt die Struktur (Provider-Namen als Keys), aber alle Werte sind ohne den Key unlesbar.

**CSRF-Schutz:** State-Token mit 10-Minuten-TTL. Nach Ablauf oder unbekanntem State wird der Callback abgelehnt.

**Auto-Refresh:** `get_token()` prueft den Ablaufzeitpunkt und erneuert den Token transparent, falls er in weniger als 60 Sekunden ablaueft.

## API Endpoints

| Endpoint | Method | Beschreibung |
|----------|--------|-------------|
| `/api/oauth/{provider}/auth-url` | GET | Auth-URL generieren. Query-Parameter: `redirect_uri`, `scopes` (kommagetrennt) |
| `/api/oauth/{provider}/callback` | GET | OAuth-Callback — wird automatisch von Google aufgerufen nach Login |
| `/api/oauth/{provider}/status` | GET | Verbindungsstatus pruefen (`connected: true/false`) |
| `/api/oauth/{provider}/` | DELETE | Token loeschen / Verbindung trennen |

`{provider}` ist aktuell immer `google`.

### Beispiel: Auth-URL generieren

```bash
curl "http://localhost:8002/api/oauth/google/auth-url?redirect_uri=https://narnia.spdns.de:8443/api/oauth/google/callback&scopes=https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/contacts"
```

### Beispiel: Status pruefen

```bash
curl http://localhost:8002/api/oauth/google/status
# {"provider": "google", "connected": true}
```

### Beispiel: Verbindung trennen

```bash
curl -X DELETE http://localhost:8002/api/oauth/google/
```

## Verfuegbare Google Scopes

| Konstante in `google.py` | Scope-URL | Beschreibung |
|--------------------------|-----------|-------------|
| `SCOPES_CALENDAR` | `auth/calendar` | Kalender lesen und schreiben |
| `SCOPES_CONTACTS` | `auth/contacts` | Kontakte lesen und schreiben |
| `SCOPES_DRIVE_READONLY` | `auth/drive.readonly` | Drive nur lesen |
| `SCOPES_TASKS` | `auth/tasks` | Google Tasks |
| `SCOPES_PROFILE` | `openid`, `userinfo.email`, `userinfo.profile` | Benutzerprofil |

**Hinweis:** Google stellt einen Refresh-Token nur einmal aus. Alle benoetigen Scopes muessen beim ersten Auth-Flow angegeben werden. Ein nachtraegliches Hinzufuegen erfordert einen neuen OAuth-Flow.

## Neuen Provider hinzufuegen

1. Neue Datei anlegen, z.B. `aifred/lib/oauth/myprovider.py`
2. Klasse von `OAuthProvider` ableiten und drei Methoden implementieren:
   ```python
   class MyProvider(OAuthProvider):
       name = "myprovider"

       def get_auth_url(self, scopes: list[str], redirect_uri: str, state: str) -> str: ...
       async def exchange_code(self, code: str, redirect_uri: str) -> TokenSet: ...
       async def refresh(self, token_set: TokenSet) -> TokenSet: ...
   ```
3. Provider beim Start registrieren (z.B. in `aifred/app_startup.py`):
   ```python
   from aifred.lib.oauth.myprovider import MyProvider
   from aifred.lib.oauth.broker import oauth_broker
   oauth_broker.register(MyProvider())
   ```
4. Der Broker erstellt automatisch die API-Endpoints unter `/api/oauth/myprovider/...`
