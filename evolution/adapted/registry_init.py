"""
evolution/adapted/registry_init.py
=====================================
Auto-registration of all 7 hand-crafted signal adapters.

Import this module (or import evolution.adapted) to register all signals.
Called from evolution/__init__.py so that any code doing:

    from evolution.signal_registry import registry

...already has the 7 adapted signals present.

Registration is idempotent: importing this module multiple times is safe
because SignalRegistry.register_cls() uses overwrite=False by default,
but we pass overwrite=True here so hot-reloads in development don't raise.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register_all() -> None:
    """Register all 7 adapted signals. Safe to call multiple times."""
    from evolution.signal_registry import registry

    from evolution.adapted.b1_signal    import B1Signal
    from evolution.adapted.b2_signal    import B2Signal
    from evolution.adapted.blk_signal   import BLKSignal
    from evolution.adapted.blkb2_signal import BLKB2Signal
    from evolution.adapted.dz30_signal  import DZ30Signal
    from evolution.adapted.scb_signal   import SCBSignal
    from evolution.adapted.s1_signal    import S1Signal

    _signals = [
        ("b1",    B1Signal,    "B1多因子买入评分 (39条件综合)"),
        ("b2",    B2Signal,    "B2超卖反弹买入评分"),
        ("blk",   BLKSignal,   "暴力K买入信号"),
        ("blkb2", BLKB2Signal, "BLKB2复合买入信号"),
        ("dz30",  DZ30Signal,  "单针30买入信号"),
        ("scb",   SCBSignal,   "SCB沙尘暴买入信号"),
        ("s1",    S1Signal,    "S1卖出信号 (负分=看空)"),
    ]

    for name, cls, desc in _signals:
        try:
            registry.register_cls(
                name=name,
                cls=cls,
                source="adapted",
                version="1.0.0",
                description=desc,
                overwrite=True,
            )
            logger.debug("Registered adapted signal: %s", name)
        except Exception as exc:
            logger.error("Failed to register adapted signal '%s': %s", name, exc)

    logger.info(
        "evolution.adapted: registered %d adapted signals → %r",
        len(_signals), registry,
    )


# ── Auto-execute on import ────────────────────────────────────────────────────
register_all()
