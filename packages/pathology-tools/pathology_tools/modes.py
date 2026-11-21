"""User-selectable approaches for a nuclear segmentation and typing session."""
import argparse
from pathlib import Path
import sys


# Public names and labels map to the mode IDs used in existing session receipts.
MODES = {
    "tool-guided": ("工具導引", "tools", "Inspect the image and combine tool contours."),
    "model-guided": ("模型導引", "raw", "Start with a blank mask and draw or write image-processing code."),
    "hybrid": ("混合", "hybrid", "A reviewer selects the starting mask. The operator compares contours before assigning cell types."),
}


def mode_key(value):
    value = value.strip().lower()
    for key, (label, engine, _) in MODES.items():
        if value in (key, label, engine):
            return key
    raise argparse.ArgumentTypeError("Choose tool-guided, model-guided or hybrid.")


def configure(parser):
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.description = "Choose a mode for visual inspection, nucleus counting and cell typing."
    parser.epilog = "\n".join(
        f"  {key}  ({label})  {description}"
        for key, (label, _, description) in MODES.items()
    )
    parser.add_argument("--mode", type=mode_key, metavar="MODE", help="Mode name. Omit to choose interactively.")
    parser.add_argument("--fields", nargs="+", type=Path, help="Prepared field directories")
    parser.add_argument("--output", type=Path, help="New output directory")
    parser.add_argument("--detector", type=Path, help="Extra candidate masks for tool-guided or hybrid review")
    parser.add_argument("--classes", nargs="+", help="Cell classes in label order")
    parser.add_argument("--python", default="python3", help="Python executable for model-authored image operations")


def ask(parser, question):
    print(question, end=" ", file=sys.stderr, flush=True)
    try:
        return input().strip()
    except EOFError:
        parser.exit(2, "\nEnd of input.\n")
    except KeyboardInterrupt:
        parser.exit(130, "\nCancelled.\n")


def select(parser):
    numbers = {}
    for index, (key, (label, _, description)) in enumerate(MODES.items(), 1):
        numbers[str(index)] = key
        print(f"{index}. {key} ({label})  {description}", file=sys.stderr)
    while True:
        answer = ask(parser, "Enter a mode name or number:")
        try:
            return mode_key(numbers.get(answer, answer))
        except argparse.ArgumentTypeError as exc:
            print(exc, file=sys.stderr)


def start(parser, args, host, individual):
    if not sys.stdin.isatty() and not (args.mode and args.fields and args.output):
        parser.error("Noninteractive use requires --mode, --fields and --output.")

    key = args.mode or select(parser)
    label, engine, _ = MODES[key]
    if not args.fields:
        args.fields = []
        while True:
            question = "Field directory:" if not args.fields else "Next field directory (Enter to finish):"
            value = ask(parser, question)
            if value:
                args.fields.append(Path(value).expanduser())
            elif args.fields:
                break
    while not args.output:
        value = ask(parser, "New output directory:")
        if value:
            args.output = Path(value).expanduser()

    if len({field.name for field in args.fields}) != len(args.fields):
        parser.error("Give each field directory a unique name.")
    for field in args.fields:
        if not (field / "source.png").is_file():
            parser.error(f"Missing source image: {field / 'source.png'}")
        if engine != "raw" and not (field / "field.json").is_file():
            parser.error(f"Prepare tool candidates for {field} before running {key} mode.")

    from .free_edit import DEFAULT_CLASSES
    if engine == "hybrid":
        from .reviewed_hybrid import run
    else:
        from .free_session import run

    args.mode = engine
    if args.classes is None:
        args.classes = DEFAULT_CLASSES
    print(f"Review mode: {key}", file=sys.stderr)
    return run(host, individual, args)
