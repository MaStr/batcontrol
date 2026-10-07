"""NextLogic - alias for DefaultLogic.

Peak shaving moved into DefaultLogic, so "next" no longer adds any
behaviour of its own. The class is kept so existing configurations with
``type: next`` keep working unchanged.
"""
from .default import DefaultLogic


class NextLogic(DefaultLogic):
    """Deprecated alias for :py:class:`DefaultLogic`; no own behaviour."""
