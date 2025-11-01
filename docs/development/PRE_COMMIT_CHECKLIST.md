# Pre-Commit Checklist

Führe diese Checks **VOR jedem Git Commit** aus, um Code-Qualität sicherzustellen.

## ⚡ Quick Check (2-3 Minuten)

```bash
# 1. Code-Style prüfen (ruff)
ruff check aifred/

# 2. Type-Hints prüfen (mypy) - OPTIONAL
mypy aifred/ --ignore-missing-imports

# 3. Tests laufen lassen - OPTIONAL (benötigt Ollama!)
pytest tests/ -v
```

## 📋 Detaillierte Schritte

### 1. **ruff** - Code-Style Checker

```bash
source venv/bin/activate
ruff check aifred/
```

**Was wird geprüft:**
- ✅ Zeilen zu lang (max 120 Zeichen)
- ✅ Ungenutzte Imports
- ✅ Falsche Einrückung
- ✅ PEP8-Violations

**Bei Fehlern:**
```bash
# Automatisch beheben (wo möglich):
ruff check aifred/ --fix
```

---

### 2. **mypy** - Type-Hint Checker (OPTIONAL)

```bash
mypy aifred/ --ignore-missing-imports
```

**Was wird geprüft:**
- ✅ Fehlende Type-Hints
- ✅ Typ-Inkonsistenzen
- ✅ Falsche Return-Types

**Bei Fehlern:**
- Manuell Type-Hints hinzufügen

---

### 3. **pytest** - Unit-Tests (OPTIONAL)

```bash
pytest tests/ -v
```

**Voraussetzung:**
- ✅ Ollama läuft (`ollama serve`)
- ✅ qwen3:8b Modell geladen

**Bei Fehlern:**
- Tests debuggen oder Code fixen

---

## 🚀 Installation der Tools

```bash
source venv/bin/activate
pip install ruff mypy pytest
```

---

## ✅ Workflow-Empfehlung

```bash
# 1. Code schreiben
vim aifred/lib/my_module.py

# 2. Quick-Check
ruff check aifred/

# 3. Falls alles grün: Commit
git add .
git commit -m "Add my_module feature"

# 4. Push
git push
```

---

## 📚 Weitere Infos

- **ruff:** https://docs.astral.sh/ruff/
- **mypy:** https://mypy.readthedocs.io/
- **pytest:** https://docs.pytest.org/

---

**Maintainer:** mp
**Letzte Aktualisierung:** 2025-11-01
