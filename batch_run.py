#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, pathlib, sys, concurrent.futures as cf
from typing import Any, Dict
from payroll_pipeline import call_gpt5_compute_payroll

OUTDIR = pathlib.Path(__file__).parent / "outputs"
OUTDIR.mkdir(exist_ok=True, parents=True)

def _safe_slug(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum() or ch in ("-", "_")).strip("_")

def process_record(idx: int, payload: Dict[str, Any], missing_policy: str) -> Dict[str, Any]:
    return call_gpt5_compute_payroll(payload, missing_policy=missing_policy)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to JSONL with one PayrollInput per line")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers (default: 1)")
    parser.add_argument("--model", default=None, help="Override OPENAI_MODEL env var (e.g., gpt-5)")
    parser.add_argument("--missing-policy", choices=["ask","default","fail"], default="fail",
                        help="Cómo resolver datos faltantes: ask|default|fail")
    args = parser.parse_args()

    if args.model:
        os.environ["OPENAI_MODEL"] = args.model

    in_path = pathlib.Path(args.input)
    if not in_path.exists():
        print(f"Input not found: {in_path}", file=sys.stderr)
        sys.exit(1)

    records = []
    with in_path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                records.append((line_num, obj))
            except json.JSONDecodeError as e:
                print(f"[SKIP] Line {line_num} JSON error: {e}", file=sys.stderr)

    if not records:
        print("No valid records found.", file=sys.stderr)
        sys.exit(2)

    results, errors = [], []
    if args.workers > 1:
        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            fut2meta = {ex.submit(process_record, i, obj, args.missing_policy): (i, obj) for i, obj in records}
            for fut in cf.as_completed(fut2meta):
                i, obj = fut2meta[fut]
                try:
                    res = fut.result()
                    results.append((i, obj, res))
                except Exception as e:
                    errors.append((i, obj, str(e)))
    else:
        for i, obj in records:
            try:
                res = process_record(i, obj, args.missing_policy)
                results.append((i, obj, res))
            except Exception as e:
                errors.append((i, obj, str(e)))

    for (i, obj, res) in results:
        year = obj.get("period", {}).get("year", "YYYY")
        month = obj.get("period", {}).get("month", "MM")
        ccaa = obj.get("region_config", {}).get("ccaa", "CCAA")
        name = f"{i:03d}_{_safe_slug(ccaa)}_{month}-{year}.json"
        out_path = OUTDIR / name
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(f"[OK] {name}")

    if errors:
        err_path = OUTDIR / "errors.ndjson"
        with err_path.open("w", encoding="utf-8") as f:
            for (i, obj, msg) in errors:
                f.write(json.dumps({"index": i, "error": msg}, ensure_ascii=False) + "\n")
        print(f"[DONE with errors] {len(results)} ok, {len(errors)} errors → {err_path}", file=sys.stderr)
        sys.exit(3)

    print(f"[DONE] {len(results)} ok, 0 errors")

if __name__ == "__main__":
    main()
