"""Approximate token pricing for Claude models.

Prices are US dollars per **million** tokens. These are list prices and change
over time — treat the cost numbers as estimates, not billing. Override via
``PRICING`` if you have negotiated or updated rates.

Cache-write (a.k.a. cache-creation) is more expensive than base input; cache
reads are much cheaper. Claude Code leans on prompt caching heavily, so the
cache columns dominate real cost — which is exactly the KV-cache-heavy behavior
the workload-characteristics paper describes.
"""

from __future__ import annotations

# model-id substring -> (input, output, cache_write, cache_read) per 1M tokens
PRICING: dict[str, tuple[float, float, float, float]] = {
    "opus":   (15.00, 75.00, 18.75, 1.50),
    "sonnet": (3.00,  15.00, 3.75,  0.30),
    "haiku":  (1.00,  5.00,  1.25,  0.10),
    # fallback if a model id matches nothing below
    "_default": (3.00, 15.00, 3.75, 0.30),
}


def _rates(model: str | None) -> tuple[float, float, float, float]:
    m = (model or "").lower()
    for key, rates in PRICING.items():
        if key != "_default" and key in m:
            return rates
    return PRICING["_default"]


def turn_cost(model: str | None, usage: dict) -> float:
    """Estimated USD cost of a single assistant turn from its ``usage`` block."""
    inp, out, cw, cr = _rates(model)
    u = usage or {}
    return (
        u.get("input_tokens", 0) * inp
        + u.get("output_tokens", 0) * out
        + u.get("cache_creation_input_tokens", 0) * cw
        + u.get("cache_read_input_tokens", 0) * cr
    ) / 1_000_000
