"""The parent dashboard / overlord (plan 0005).

Serves the Claude Design export in `UI/` untouched, feeds it from the voice receptionist's call
records and the marketing agent's ScanBrief, and routes the page's actions back to both agents.
Import surface: `dashboard.app.mount_dashboard(app, config, voice_deps)`.
"""
