"""Data-source connectors. Source-specific HTTP lives here; domain knowledge does not."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from core.connectors.base import Connector, dedup
from core.connectors.clinicaltrials import ClinicalTrialsConnector
from core.connectors.guidelines import GuidelinesConnector, PdqConnector
from core.connectors.openfda import OpenFdaConnector
from core.connectors.pubmed import PubMedConnector

if TYPE_CHECKING:  # avoid an import cycle at runtime
    from core.config.vertical import VerticalConfig

__all__ = [
    "Connector",
    "dedup",
    "PubMedConnector",
    "ClinicalTrialsConnector",
    "OpenFdaConnector",
    "GuidelinesConnector",
    "PdqConnector",
    "CONNECTOR_REGISTRY",
    "default_connectors",
    "connectors_for_vertical",
]

# Registry of available connector classes keyed by their stable ``source`` name.
# Keeping the class implementations here is fine — the boundary rule (C4) is that
# the SELECTION of which sources run must be config-driven, not the classes. The
# registry is the menu; the vertical's ``connectors.sources`` is the order.
CONNECTOR_REGISTRY: dict[str, Callable[[], Connector]] = {
    PubMedConnector.source: PubMedConnector,
    ClinicalTrialsConnector.source: ClinicalTrialsConnector,
    OpenFdaConnector.source: OpenFdaConnector,
    PdqConnector.source: PdqConnector,
}


def default_connectors() -> list[Connector]:
    """Full free-source connector set — every registered connector.

    This is a last-resort fallback used only when no vertical selection is
    available; normal runs build the set from the active vertical via
    ``connectors_for_vertical``.
    """
    return [factory() for factory in CONNECTOR_REGISTRY.values()]


def connectors_for_vertical(vertical: "VerticalConfig") -> list[Connector]:
    """Build the connector instances the vertical selects (boundary C4).

    Maps each configured source name to its registered connector class, in the
    order the vertical lists them. Unknown names are skipped (a vertical may name
    a source a future build adds). If the vertical configures no selection, fall
    back to the full registry so the engine still functions.
    """
    sources = vertical.connector_sources
    if not sources:
        return default_connectors()
    built: list[Connector] = []
    for name in sources:
        factory = CONNECTOR_REGISTRY.get(name)
        if factory is not None:
            built.append(factory())
    return built or default_connectors()
