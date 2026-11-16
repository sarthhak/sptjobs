from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .config import Settings
from .store import Store


@dataclass
class AppContext:
    settings: Settings
    store: Store
    ga4: Any            # GA4Client (or a fake in tests)
    gtm: Any            # GTMClient (or a fake in tests)
    anthropic: Any      # anthropic.AsyncAnthropic, or None if no API key is configured
    verify_site: Callable[..., Awaitable[dict]]
    google_status: Callable[[], str] = field(default=lambda: "unknown")
    google_reset: Callable[[], None] = field(default=lambda: None)
