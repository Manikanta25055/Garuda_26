"""Narada's brain: who Narada is, what it carries from one turn to the next,
and what it refuses to give away.

The agent loop and the house tools stay in garuda_auto/agent.py; this package
is what that loop is given to think with. One brain serves both products
(Garuda, the security system, and Drishti, the home), and one house: there is
a single owner household per installation, so nothing here is keyed by tenant.

The design is the one worked out for the bREADth assistant (a context budget
that carries whole messages or none, a rolling summary, guards that are code
and not prose in a prompt), rewritten for this house.
"""
from . import guards, noticing, persona
from .brain import Brain

__all__ = ["Brain", "guards", "noticing", "persona"]
