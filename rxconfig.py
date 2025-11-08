"""Reflex configuration for AIfred Intelligence"""

import reflex as rx
import os
import asyncio

# Entwicklung: Hauptrechner (WSL) - Zugriff nur lokal via localhost
# Produktion: Mini PC (natives Ubuntu) - Zugriff im Netzwerk via 192.168.0.252

# Bestimme Umgebung basierend auf Umgebungsvariable oder Standardwert
is_prod = os.getenv("AIFRED_ENV", "dev") == "prod"

config = rx.Config(
    app_name="aifred",
    backend_host="0.0.0.0",  # Listen on all interfaces
    backend_port=8002,
    frontend_port=3002,
    frontend_host="0.0.0.0",  # Frontend auch auf allen Interfaces!
    # Dynamische API-URL basierend auf Umgebung
    api_url="https://narnia.spdns.de:8443" if is_prod else "http://172.30.8.72:8002",
    env=rx.Env.PROD if is_prod else rx.Env.DEV,
    # Sitemap-Plugin deaktivieren (wir brauchen keine Sitemap)
    disable_plugins=["reflex.plugins.sitemap.SitemapPlugin"],
    # Performance-Optimierungen
    compile_timeout=90,  # Timeout für Compilation erhöhen (default: 60s)
)

# Für Mini PC (natives Ubuntu, 192.168.0.252):
# Ersetze localhost durch: http://192.168.0.252:8002 bzw. :3002
