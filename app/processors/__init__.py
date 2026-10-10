"""Discover processor modules and expose the shared operation registry."""

from importlib import import_module
from pkgutil import iter_modules

from app.processors.base import registry

for module in iter_modules(__path__):
    if module.name != "base" and not module.name.startswith("_"):
        import_module(f"{__name__}.{module.name}")

__all__ = ["registry"]
