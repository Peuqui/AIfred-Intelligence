# Service Restart ohne sudo

## Problem
Services können im laufenden Betrieb "hängen bleiben":
- **Ollama:** Zu große Prompts, Memory-Probleme, GPU-Fehler
- **AIfred Intelligence:** Code-Updates, Memory-Leaks, Stuck Gradio

Ein Neustart erfordert normalerweise `sudo systemctl restart <service>`, was von der Weboberfläche aus nicht möglich ist.

## Lösung: polkit-Regel

Mit einer polkit-Regel kann der User `mp` den Ollama-Service ohne sudo-Passwort neustarten.

### Installation

1. **polkit-Regel erstellen:**

```bash
sudo nano /etc/polkit-1/rules.d/99-ollama-restart.rules
```

2. **Folgenden Inhalt einfügen:**

```javascript
// Allow user 'mp' to restart ollama.service without password
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        action.lookup("unit") == "ollama.service" &&
        (action.lookup("verb") == "restart" || action.lookup("verb") == "stop" || action.lookup("verb") == "start") &&
        subject.user == "mp") {
        return polkit.Result.YES;
    }
});
```

3. **Berechtigungen setzen:**

```bash
sudo chmod 644 /etc/polkit-1/rules.d/99-ollama-restart.rules
```

4. **polkit neu laden (optional):**

```bash
sudo systemctl restart polkit
```

### Test

```bash
# Als User 'mp' (OHNE sudo):
systemctl restart ollama.service
```

Sollte jetzt **ohne Passwortabfrage** funktionieren!

### Nutzung in AIfred Intelligence

1. Öffne AIfred Intelligence im Browser
2. Klappe "🐛 Debug Console" auf
3. Drei Buttons verfügbar:
   - **🔄 Console aktualisieren** - Lädt Debug-Console neu
   - **🔄 Ollama neu starten** - Startet Ollama-Service neu
   - **🔄 AIfred neu starten** - Startet AIfred-Intelligence-Service neu
4. Nach Klick: Status wird angezeigt

**Wann welcher Button?**
- **Ollama hängt** (zu viel Context, GPU-Problem) → Ollama neu starten
- **Code geändert** (Python, Prompts) → AIfred neu starten
- **Stuck Gradio** oder Memory-Leak → AIfred neu starten

**Funktioniert auch vom Handy aus!** 📱

## Sicherheit

Die polkit-Regel erlaubt **NUR**:
- User: `mp` (nicht root, nicht andere User)
- Service: `ollama.service` (nicht andere Services)
- Actions: `start`, `stop`, `restart` (nicht `disable`, `enable`, etc.)

## Fehlerbehebung

### "🔒 Keine Berechtigung"
- polkit-Regel prüfen: `cat /etc/polkit-1/rules.d/99-ollama-restart.rules`
- Berechtigungen prüfen: `ls -l /etc/polkit-1/rules.d/99-ollama-restart.rules`
- polkit neu starten: `sudo systemctl restart polkit`

### "⏱️ Timeout"
- Ollama-Service Status prüfen: `systemctl status ollama.service`
- Journal-Logs prüfen: `journalctl -u ollama.service -n 50`

### Test manuell
```bash
# Als User 'mp':
systemctl restart ollama.service
echo $?  # Sollte 0 sein
```

## Alternative: User-Space Ollama

Falls polkit nicht funktioniert, kann Ollama auch im User-Space laufen:

⚠️ **Nachteil:** ROCm GPU-Zugriff könnte komplizierter sein!

```bash
# Service stoppen
sudo systemctl stop ollama.service
sudo systemctl disable ollama.service

# User-Service erstellen
mkdir -p ~/.config/systemd/user/
cp /etc/systemd/system/ollama.service ~/.config/systemd/user/

# User-Service starten
systemctl --user enable ollama.service
systemctl --user start ollama.service

# Restart ohne sudo
systemctl --user restart ollama.service
```

## Referenzen

- [polkit Documentation](https://www.freedesktop.org/software/polkit/docs/latest/)
- [systemd User Services](https://wiki.archlinux.org/title/Systemd/User)
