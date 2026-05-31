"""
evolution/adapted — Hand-crafted signal adapters
=================================================

Each module wraps one existing scoring function from signals/singal_cal/
into a BaseSignal subclass, so the evolution system can treat them
identically to LLM-generated signals.

Import this package to auto-register all 7 adapted signals:
    import evolution.adapted   # side-effect: registers all 7 signals

Or import individually:
    from evolution.adapted.b1_signal import B1Signal
"""
