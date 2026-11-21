"""Host CLI with an optional native composition stage."""
import sys


def main():
    if sys.argv[1:2] == ['compose']:
        from .toolbox import POLICY
        from pathology_tools.composition_cli import cli
        return cli(POLICY, True, sys.argv[2:])
    from .cli import main as original
    return original()


if __name__ == '__main__':
    main()
