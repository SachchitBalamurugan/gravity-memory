"""Shared, packaged console assets."""
from importlib import resources
from functools import lru_cache
import re

MEDIA = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8",
         "png": "image/png", "html": "text/html; charset=utf-8"}

@lru_cache(maxsize=32)
def asset(route):
    name = {"": "index.html", "favicon.ico": "icon.png"}.get(route, route)
    if not re.fullmatch(r"[a-z0-9_-]+\.(html|js|css|png)", name):
        return None
    path = resources.files("gravity_memory").joinpath("web", name)
    return (path.read_bytes(), MEDIA[name.rsplit(".", 1)[1]]) if path.is_file() else None
