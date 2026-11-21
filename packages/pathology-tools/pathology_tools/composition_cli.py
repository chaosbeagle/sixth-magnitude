"""CLI adapter for the optional composition stage."""
import argparse
from pathlib import Path
from .composition_bench import Bench, PROMPT, definitions
from .codex_session import run


def execute(paths,output,session,host_policy,individual,local,detector,budget=6):
    if budget < 1:
        raise ValueError("Budget must be positive")
    policy={**host_policy,'individual':individual,'local':local,'budget':budget,
            'detector':str(Path(detector).resolve()) if detector else None,
            'workflow':'individual-composition' if individual else 'batch-composition'}
    return run(paths,output,policy,session,bench_factory=Bench,definitions=definitions,prompt=PROMPT)


def cli(policy,individual,argv):
    p=argparse.ArgumentParser(prog=('sixth-magnitude' if individual else 'sols-hammer')+' compose')
    p.add_argument('fields',nargs='+',type=Path,help='Prepared native field packets')
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--session',required=True,type=Path)
    p.add_argument('--detector',type=Path,help='Directory with verified StarDist candidates')
    p.add_argument('--whole-only',action='store_true')
    p.add_argument('--budget',type=int,default=6)
    a=p.parse_args(argv)
    print(execute(a.fields,a.output,a.session,policy,individual,not a.whole_only,a.detector,a.budget))
