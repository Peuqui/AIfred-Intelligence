# Übergabe an Mini-PC: polkit-Regeln installieren

## Status
Auf dem **Hauptrechner (Aragon)** wurden Service-Restart-Buttons implementiert und getestet. Diese Änderungen müssen nun auch auf dem **Mini-PC** installiert werden.

## Was wurde implementiert?

### 1. Service Restart Buttons in AIfred UI
- **🔄 Ollama neu starten** - Startet ollama.service ohne sudo
- **🔄 AIfred neu starten** - Startet aifred-intelligence.service ohne sudo

### 2. Code-Änderungen (bereits im Git)
- `aifred_intelligence.py`: Zwei neue Restart-Buttons in Debug Console
- `docs/OLLAMA_RESTART_SETUP.md`: Vollständige Dokumentation

### 3. polkit-Regeln (müssen manuell installiert werden)
Zwei polkit-Regeln ermöglichen Service-Restarts ohne sudo-Passwort.

---

## Installation auf Mini-PC

### Schritt 1: Repository aktualisieren

```bash
cd /home/mp/Projekte/AIfred-Intelligence
git pull origin main
```

**Erwartete Commits:**
- `58d9faf` - "Refactor prompts to external files and optimize for phi3:mini performance"
- `64daaf3` - "Add service restart buttons for Ollama and AIfred Intelligence"

### Schritt 2: polkit-Regeln erstellen

**Regel 1: Ollama-Restart** (`/tmp/99-ollama-restart.rules`)

```bash
cat > /tmp/99-ollama-restart.rules << 'EOF'
// Allow user 'mp' to restart ollama.service without password
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        action.lookup("unit") == "ollama.service" &&
        (action.lookup("verb") == "restart" || action.lookup("verb") == "stop" || action.lookup("verb") == "start") &&
        subject.user == "mp") {
        return polkit.Result.YES;
    }
});
EOF
```

**Regel 2: AIfred-Restart** (`/tmp/99-aifred-restart.rules`)

```bash
cat > /tmp/99-aifred-restart.rules << 'EOF'
// Allow user 'mp' to restart aifred-intelligence.service without password
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        action.lookup("unit") == "aifred-intelligence.service" &&
        (action.lookup("verb") == "restart" || action.lookup("verb") == "stop" || action.lookup("verb") == "start") &&
        subject.user == "mp") {
        return polkit.Result.YES;
    }
});
EOF
```

### Schritt 3: polkit-Regeln installieren

```bash
# Kopiere beide Regeln nach /etc/polkit-1/rules.d/
sudo cp /tmp/99-ollama-restart.rules /etc/polkit-1/rules.d/
sudo cp /tmp/99-aifred-restart.rules /etc/polkit-1/rules.d/

# Setze korrekte Berechtigungen
sudo chmod 644 /etc/polkit-1/rules.d/99-ollama-restart.rules
sudo chmod 644 /etc/polkit-1/rules.d/99-aifred-restart.rules

# Optional: polkit neu laden
sudo systemctl restart polkit
```

### Schritt 4: Testen

**Test 1: Ollama-Restart ohne sudo**
```bash
systemctl restart ollama.service
echo $?  # Sollte 0 sein (kein Fehler)
```

**Test 2: AIfred-Restart ohne sudo**
```bash
systemctl restart aifred-intelligence.service
echo $?  # Sollte 0 sein (kein Fehler)
```

**Beide Befehle sollten OHNE Passwort-Abfrage funktionieren!**

### Schritt 5: Buttons in UI testen

1. Öffne AIfred Intelligence im Browser: `http://localhost:7860`
2. Klappe **"🐛 Debug Console"** auf
3. Prüfe ob 3 Buttons sichtbar sind:
   - 🔄 Console aktualisieren
   - 🔄 Ollama neu starten ← **NEU**
   - 🔄 AIfred neu starten ← **NEU**
4. Teste beide Restart-Buttons:
   - Klick auf "Ollama neu starten" → Status: "✅ Ollama neu gestartet"
   - Klick auf "AIfred neu starten" → Status: "✅ Restart läuft - Seite neu laden!"

### Schritt 6: Service-Status prüfen

```bash
# Prüfe ob Services laufen
systemctl status ollama.service
systemctl status aifred-intelligence.service

# Prüfe Journal-Logs für Restart-Bestätigung
journalctl -u ollama.service --since "1 minute ago" | grep -E "(Stopping|Started)"
journalctl -u aifred-intelligence.service --since "1 minute ago" | grep -E "(Restart|angefordert)"
```

---

## Für Backup & Restore Skript

### Dateien die gesichert werden müssen:

**polkit-Regeln** (neu):
```
/etc/polkit-1/rules.d/99-ollama-restart.rules
/etc/polkit-1/rules.d/99-aifred-restart.rules
```

**Ergänze im Backup-Skript:**
```bash
# Backup polkit-Regeln
sudo cp /etc/polkit-1/rules.d/99-ollama-restart.rules /backup/polkit/ 2>/dev/null || true
sudo cp /etc/polkit-1/rules.d/99-aifred-restart.rules /backup/polkit/ 2>/dev/null || true
```

**Ergänze im Restore-Skript:**
```bash
# Restore polkit-Regeln
if [ -f /backup/polkit/99-ollama-restart.rules ]; then
    sudo cp /backup/polkit/99-ollama-restart.rules /etc/polkit-1/rules.d/
    sudo chmod 644 /etc/polkit-1/rules.d/99-ollama-restart.rules
fi

if [ -f /backup/polkit/99-aifred-restart.rules ]; then
    sudo cp /backup/polkit/99-aifred-restart.rules /etc/polkit-1/rules.d/
    sudo chmod 644 /etc/polkit-1/rules.d/99-aifred-restart.rules
fi

# polkit neu laden
sudo systemctl restart polkit 2>/dev/null || true
```

---

## Warum ist das wichtig?

### Use Cases:
- **Remote-Management vom Handy** - Kein SSH mehr nötig für Service-Restarts
- **Ollama hängt** (zu viel Context bei Routenplanung) → Restart-Button klicken
- **Code/Prompt-Änderungen** → AIfred neu starten ohne Terminal
- **Stuck Gradio oder Memory-Leaks** → AIfred-Restart vom Handy aus

### Sicherheit:
- ✅ Nur User `mp` hat Rechte
- ✅ Nur diese 2 Services (nicht andere)
- ✅ Nur restart/start/stop (nicht disable/enable/mask)
- ✅ Kein root-Zugriff

---

## Fehlerbehebung

### Problem: "Interactive authentication required"
**Lösung:** polkit-Regeln fehlen oder sind fehlerhaft installiert
```bash
# Prüfe ob Regeln existieren
ls -la /etc/polkit-1/rules.d/99-*restart.rules

# Prüfe Regel-Syntax
sudo cat /etc/polkit-1/rules.d/99-ollama-restart.rules
sudo cat /etc/polkit-1/rules.d/99-aifred-restart.rules

# polkit neu laden
sudo systemctl restart polkit
```

### Problem: Buttons nicht sichtbar
**Lösung:** AIfred-Service mit altem Code läuft noch
```bash
# Service neu starten (lädt neuen Code)
sudo systemctl restart aifred-intelligence.service

# Oder über neuen Button (wenn der schon sichtbar ist)
```

### Problem: "Permission denied" trotz Regel
**Lösung:** User nicht in Regel eingetragen oder Tippfehler
```bash
# Prüfe Username
whoami  # Sollte "mp" sein

# Prüfe Regel-Inhalt
sudo cat /etc/polkit-1/rules.d/99-ollama-restart.rules | grep "subject.user"
# Sollte: subject.user == "mp"
```

---

## Dokumentation

Vollständige Dokumentation in: `docs/OLLAMA_RESTART_SETUP.md`

---

## Checklist für Mini-PC

- [ ] Git pull durchgeführt (Commits 58d9faf, 64daaf3)
- [ ] polkit-Regel für Ollama erstellt und installiert
- [ ] polkit-Regel für AIfred erstellt und installiert
- [ ] systemctl restart ollama.service OHNE sudo getestet
- [ ] systemctl restart aifred-intelligence.service OHNE sudo getestet
- [ ] AIfred-Service neu gestartet (lädt neuen Code)
- [ ] Buttons in UI sichtbar und getestet
- [ ] Backup-Skript angepasst (polkit-Regeln sichern)
- [ ] Restore-Skript angepasst (polkit-Regeln wiederherstellen)

---

## Fragen?

Bei Problemen siehe: `docs/OLLAMA_RESTART_SETUP.md`

Oder frage deinen Kollegen Claude Code auf dem Hauptrechner (Aragon) 😉

---

**Erstellt:** 2025-10-24
**Von:** Claude Code (Aragon)
**Für:** Claude Code (Mini-PC)
**Commits:** 58d9faf, 64daaf3
