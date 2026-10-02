"""What the Garuda service does in the background and to its own state:
presence, monitors, alerts, persistence. One module per concern.

Each module is bound to the live Garuda_web module (`bind(core)`) and
reads its state through it at call time; none of them imports Garuda_web.
"""
