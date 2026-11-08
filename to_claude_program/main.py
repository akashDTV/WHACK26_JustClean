"""
main.py - run to_claude on the inputs from a params file.

    python main.py                # uses params.py (or test2.py if there is no params.py)
    python main.py test3          # uses test3.py (any file defining PARAMS, COMPARISON, PERSONALITY)
    python main.py test3 --no-api # skip Claude, write the template version

Works from any folder (e.g. python to_claude_program/main.py from the repo root).

Writes exactly three files into the output folder:
    overall_summary.txt   the personality-voiced summary from Claude
    priority_fixes.json   the top 3 priority fixes (title, subheading, description, fix)
    detailed_breakdown.txt  scores by component, section and body part, with short notes
Output goes to to_claude_program/output/ for params.py, and to output/<name>/ for any
other file. A relative --out-dir is also taken from to_claude_program/.
"""
import argparse
import importlib
from pathlib import Path

from to_claude import to_claude

HERE = Path(__file__).resolve().parent
DEFAULT_MODULE = "params" if (HERE / "params.py").exists() else "test2"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("module", nargs="?", default=DEFAULT_MODULE,
                    help=f"name of the inputs file without .py (default: {DEFAULT_MODULE})")
    ap.add_argument("-o", "--out-dir", default=None)
    ap.add_argument("--no-api", action="store_true", help="skip Claude; template output only")
    args = ap.parse_args()

    out_dir = Path(args.out_dir or ("output" if args.module == "params"
                                    else Path("output") / args.module))
    if not out_dir.is_absolute():
        out_dir = HERE / out_dir          # same place whichever folder you run from

    inputs = importlib.import_module(args.module)
    result = to_claude(inputs.PARAMS, inputs.COMPARISON, inputs.PERSONALITY,
                       out_dir=out_dir, use_api=not args.no_api)

    print(f"Done: {args.module} (source: {result['source']})")
    print(f"  {result['overall_summary']}")
    print(f"  {result['priority_fixes']}")
    print(f"  {result['detailed_breakdown']}")
    for note in result["data_notes"]:
        print(f"  note: {note}")


if __name__ == "__main__":
    main()
