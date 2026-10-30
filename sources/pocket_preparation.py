"""Compatibility entry point for the frozen v4 closed-pocket contract."""
from .milling_preparation import prepare_milling


def prepare_pocket(self,manifest):
    return prepare_milling(self,manifest)
