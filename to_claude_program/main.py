"""
main.py - run to_claude on the inputs from a params file.

    python main.py                # uses params.py
    python main.py test3          # uses test3.py (any file defining PARAMS, COMPARISON, PERSONALITY)
    python main.py test3 --no-api # skip Claude, write the template version

Writes exactly three files into the output folder:
    overall_summary.txt   the personality-voiced summary from Claude
    priority_fixes.json   the top 3 priority fixes (title, subheading, description, fix)
    detailed_breakdown.txt  scores by component, section and body part, with short notes
Output goes to output/ for params.py, and to output/<name>/ for any other file.
"""
import test2

import argparse
import importlib

from to_claude import to_claude


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("module", nargs="?", default="params",
                    help="name of the inputs file without .py (default: params)")
    ap.add_argument("-o", "--out-dir", default=None)
    ap.add_argument("--no-api", action="store_true", help="skip Claude; template output only")
    args = ap.parse_args()

    out_dir = args.out_dir or ("output" if args.module == "params" else f"output/{args.module}")

    result = to_claude(test2.PARAMS, test2.COMPARISON, test2.PERSONALITY,
                       out_dir=out_dir, use_api=not args.no_api)

    print(f"Done: {args.module} (source: {result['source']})")
    print(f"  {result['overall_summary']}")
    print(f"  {result['priority_fixes']}")
    print(f"  {result['detailed_breakdown']}")
    for note in result["data_notes"]:
        print(f"  note: {note}")


if __name__ == "__main__":
    main()
