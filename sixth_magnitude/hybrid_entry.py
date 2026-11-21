"""Astra host with optional mixed image editing."""
import sys
def main():
    if sys.argv[1:2]==['hybrid']:
        from pathology_tools.free_session import cli
        return cli('astra',True,sys.argv[2:])
    from .entry import main as original
    return original()
