"""Reflex configuration for AIfred Intelligence"""

import reflex as rx

config = rx.Config(
    app_name="aifred",
    backend_host="0.0.0.0",  # Listen on all interfaces
    backend_port=8002,
    frontend_port=3002,
    api_url="http://192.168.0.252:8002",  # Mini PC IP address
    deploy_url="http://192.168.0.252:3002",
    env=rx.Env.DEV,
)
