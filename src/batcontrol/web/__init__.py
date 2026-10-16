""" Read-only web API, dashboard and MCP server for batcontrol.

The package is self contained: importing it does not pull in FastAPI unless
the API is actually enabled, so an installation that never switches it on
pays nothing for it. core.py only imports web.snapshot (no dependencies) and
constructs WebServer lazily.
"""
