"""Reflex configuration for AIfred Intelligence"""

import reflex as rx

# Entwicklung: Hauptrechner (WSL) - Zugriff nur lokal via localhost
# Produktion: Mini PC (natives Ubuntu) - Zugriff im Netzwerk via 192.168.0.252

config = rx.Config(
    app_name="aifred",
    backend_host="0.0.0.0",  # Listen on all interfaces
    backend_port=8002,
    frontend_port=3002,
    # Lokale Entwicklung (Hauptrechner WSL)
    api_url="http://localhost:8002",
    deploy_url="http://localhost:3002",
    env=rx.Env.DEV,
    # Sitemap-Plugin deaktivieren (wir brauchen keine Sitemap)
    disable_plugins=["reflex.plugins.sitemap.SitemapPlugin"],
    # Performance-Optimierungen
    compile_timeout=90,  # Timeout für Compilation erhöhen (default: 60s)
)

# Für Mini PC (natives Ubuntu, 192.168.0.252):
# Ersetze localhost durch: http://192.168.0.252:8002 bzw. :3002
