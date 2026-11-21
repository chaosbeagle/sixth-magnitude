"""Native lung inspection and regional typing."""
from .toolbox import POLICY
from pathology_tools.regional_bench import cli

def main():
    cli(POLICY,True)

if __name__ == '__main__':
    main()
