# Multi-API Search Setup Guide

## Übersicht: 4-Stufen Fallback System

Der Voice Assistant nutzt nun ein 4-stufiges Fallback-System für Web-Suche:

1. **Brave Search API** (Primary) - 2.000 kostenlose Queries/Monat
2. **Tavily AI** (Fallback 1) - 1.000 kostenlose Queries/Monat
3. **Serper.dev** (Fallback 2) - 2.500 kostenlose Queries (einmalig)
4. **SearXNG** (Last Resort) - Unlimited, self-hosted (läuft bereits!)

**Total: 3.000+ renewable Queries/Monat + unlimited Fallback**

---

## ✅ SearXNG (Last Resort) - FERTIG!

SearXNG läuft bereits und ist einsatzbereit:

- **Status:** ✅ Läuft auf `http://localhost:8888`
- **Docker:** `/home/mp/MiniPCLinux/docker/searxng/`
- **Test:** `curl "http://localhost:8888/search?q=test&format=json"`
- **Queries:** Unlimited (self-hosted, meta-search)

### SearXNG Verwaltung:

```bash
# Status prüfen
docker ps | grep searxng

# Container stoppen/starten
cd /home/mp/MiniPCLinux/docker/searxng
docker compose stop
docker compose start

# Logs ansehen
docker logs searxng -f

# Container neustarten
docker compose restart
```

---

## 🔑 API Keys Setup (Optional aber empfohlen)

Die APIs brauchen API Keys. Ohne Keys nutzt der Agent nur SearXNG (funktioniert, aber langsamer).

### 1. Brave Search API (Primary) - 2.000/Monat

**Warum?** Best privacy, bester Index (30B+ Seiten), ideal für News

**Setup:**

1. Gehe zu: https://brave.com/search/api/
2. Klicke "Get Started" → "Sign Up"
3. Email verifizieren
4. Im Dashboard: "API Keys" → "Create API Key"
5. Kopiere den Key (beginnt mit `BSA...`)

**Einrichten:**

```bash
# In ~/.bashrc oder /etc/environment
echo 'export BRAVE_API_KEY="dein_brave_api_key_hier"' >> ~/.bashrc
source ~/.bashrc

# Oder für systemd service (empfohlen):
sudo nano /etc/systemd/system/voice-assistant.service
# Füge hinzu unter [Service]:
Environment="BRAVE_API_KEY=dein_brave_api_key_hier"

sudo systemctl daemon-reload
```

**Kosten:**
- Free: 2.000 Queries/Monat (erneuert sich monatlich)
- Danach: $5/1.000 Queries (optional)

---

### 2. Tavily AI (Fallback 1) - 1.000/Monat

**Warum?** RAG-optimiert, speziell für AI/LLM gebaut, saubere Snippets

**Setup:**

1. Gehe zu: https://www.tavily.com/
2. "Get Started" → Sign up (Email oder GitHub)
3. Dashboard → "API Keys"
4. Kopiere den Key (beginnt mit `tvly-...`)

**Einrichten:**

```bash
# In ~/.bashrc
echo 'export TAVILY_API_KEY="dein_tavily_key_hier"' >> ~/.bashrc
source ~/.bashrc

# Oder systemd service:
sudo nano /etc/systemd/system/voice-assistant.service
Environment="TAVILY_API_KEY=dein_tavily_key_hier"
```

**Kosten:**
- Free: 1.000 Queries/Monat (erneuert sich monatlich)
- Danach: $0.60/1.000 Queries (sehr günstig!)

---

### 3. Serper.dev (Fallback 2) - 2.500 einmalig

**Warum?** Google-powered (beste Qualität!), günstiger als Google Custom Search

**Setup:**

1. Gehe zu: https://serper.dev/
2. "Sign Up" (Email oder Google)
3. Dashboard zeigt sofort deinen API Key
4. Kopiere den Key

**Einrichten:**

```bash
# In ~/.bashrc
echo 'export SERPER_API_KEY="dein_serper_key_hier"' >> ~/.bashrc
source ~/.bashrc

# Oder systemd service:
sudo nano /etc/systemd/system/voice-assistant.service
Environment="SERPER_API_KEY=dein_serper_key_hier"
```

**Kosten:**
- Free: 2.500 Queries (einmalig, nicht erneuerbar)
- Danach: $0.30-$1/1.000 Queries (sehr günstig!)

---

## 🚀 Komplette Installation (Alle 3 APIs)

```bash
# 1. API Keys in systemd service eintragen
sudo nano /etc/systemd/system/voice-assistant.service
```

Füge unter `[Service]` hinzu:

```ini
Environment="BRAVE_API_KEY=dein_brave_key"
Environment="TAVILY_API_KEY=dein_tavily_key"
Environment="SERPER_API_KEY=dein_serper_key"
```

```bash
# 2. Systemd neu laden
sudo systemctl daemon-reload

# 3. Voice Assistant neu starten
sudo systemctl restart voice-assistant

# 4. Logs prüfen (sollte zeigen: "✅ Brave Search API aktiviert")
sudo journalctl -u voice-assistant -f
```

---

## 🧪 Testing

### Test ohne API Keys (nur SearXNG):

```bash
cd /home/mp/Projekte/voice-assistant
source venv/bin/activate
python -c "from agent_tools import search_web; print(search_web('test')['source'])"
```

Erwartete Ausgabe: `SearXNG (Self-Hosted)`

### Test mit API Keys:

```bash
python -c "from agent_tools import search_web; result = search_web('latest Trump news'); print(f\"Source: {result['source']}, URLs: {len(result['related_urls'])}\")"
```

Erwartete Ausgabe: `Source: Brave Search, URLs: 10` (oder Tavily/Serper je nach Setup)

---

## 📊 Query Economics

**Was ist 1 Query?**
- 1 Such-Anfrage = 1 Query (egal wie viele Ergebnisse zurückkommen)
- Beispiel: "Trump news" → 10 URLs = nur 1 Query verbraucht!

**Reicht das?**
- 2.000 Brave + 1.000 Tavily = 3.000/Monat
- = ~100 Fragen/Tag
- Für persönlichen Gebrauch: **mehr als genug!**
- Falls erschöpft: SearXNG übernimmt (unlimited)

---

## 🔧 Troubleshooting

### "Alle APIs fehlgeschlagen"

```bash
# 1. Prüfe ob SearXNG läuft
curl "http://localhost:8888/search?q=test&format=json"

# 2. Prüfe API Keys
echo $BRAVE_API_KEY
echo $TAVILY_API_KEY
echo $SERPER_API_KEY

# 3. Prüfe Logs
sudo journalctl -u voice-assistant -f | grep -E "(Brave|Tavily|Serper|SearXNG)"
```

### "Rate Limit erreicht"

Normal! Das System wechselt automatisch zur nächsten API:

```
⚠️ Brave Search: Rate Limit erreicht!
🔄 Versuche: Tavily AI
✅ Tavily AI erfolgreich!
```

### "Keine URLs gefunden"

Prüfe ob SearXNG wirklich Ergebnisse liefert:

```bash
curl "http://localhost:8888/search?q=Donald+Trump&format=json" | jq '.results | length'
```

---

## 📝 Empfohlene Konfiguration

**Für beste Ergebnisse:**

1. ✅ **SearXNG installiert** (bereits ✅)
2. ✅ **Brave API Key** (Primary, beste Qualität)
3. ⚠️ **Tavily optional** (wenn RAG-optimierte Snippets gewünscht)
4. ⚠️ **Serper optional** (als zusätzliche Absicherung)

**Minimale Konfiguration (kostenlos, funktioniert):**
- Nur SearXNG (unlimited, bereits läuft!)

**Empfohlene Konfiguration (3.000 Queries/Monat):**
- Brave + Tavily + SearXNG Fallback

**Premium Konfiguration (5.500+ Queries/Monat):**
- Brave + Tavily + Serper + SearXNG Fallback

---

## 🎯 Status: Was funktioniert jetzt?

✅ SearXNG läuft (unlimited, self-hosted)
✅ Multi-API System implementiert
✅ Automatisches Fallback bei Rate Limits
✅ Agent Tools aktualisiert
⏳ API Keys noch nicht konfiguriert (optional)
⏳ Service noch nicht neu gestartet

**Nächste Schritte:**
1. API Keys besorgen (optional, aber empfohlen)
2. Keys in systemd service eintragen
3. Service neu starten
4. Mit Trump-News-Query testen!

---

**Erstellt:** 2025-10-13
**Author:** Claude Code
**Version:** 1.0 - Multi-API Fallback System
