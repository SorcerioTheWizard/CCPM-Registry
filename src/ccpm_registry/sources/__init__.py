"""
CCPM External Sources

The external catalogs `ccpm-registry sync` can mirror, keyed by the prefix their packages are published under.
"""

# MARK: Imports
from ccpm_registry.sources.base import Source
from ccpm_registry.sources.pinestore import PinestoreSource

# MARK: Constants
SOURCES: dict[str, type[Source]] = {
    PinestoreSource.name: PinestoreSource,
}
