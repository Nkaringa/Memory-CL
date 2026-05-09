"""Static-asset UI package — Phase-9 read-only inspection dashboard.

The actual UI is plain HTML + vanilla JS under `static/`. Mounting
is handled by `apps.api.main.create_app()` via FastAPI's
`StaticFiles`. This module exists so packaging tools recognize the
folder as a Python package.
"""
