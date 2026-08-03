"""Registry of available external data-source adapters."""

from .metmuseum import MetMuseumAdapter


SOURCE_ADAPTERS = {
    MetMuseumAdapter.source_key: MetMuseumAdapter,
}

__all__ = ["SOURCE_ADAPTERS", "MetMuseumAdapter"]
