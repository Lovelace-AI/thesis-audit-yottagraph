"""
CLI entry point for the research learner.

Usage:
    cd agents
    python -m research_learner.run --hours 8 --seed-from-file my_prompt.txt
    python -m research_learner.run --iterations 20 --query netflix_is_losing...
    python -m research_learner.run --history
    python -m research_learner.run --report
    python -m research_learner.run --export-best
"""

import argparse
import sys
from pathlib import Path


def cmd_run(args: argparse.Namespace) -> None:
    from research_learner.learner import run_learner

    seed_prompt = None
    if args.seed_from_file:
        seed_path = Path(args.seed_from_file)
        if not seed_path.exists():
            print(f"Error: seed file not found: {seed_path}")
            sys.exit(1)
        seed_prompt = seed_path.read_text().strip()
        print(f"Using seed prompt from: {seed_path} ({len(seed_prompt)} chars)")

    db_path = Path(args.db) if args.db else None

    run_learner(
        iterations=args.iterations,
        hours=args.hours,
        db_path=db_path,
        seed_prompt=seed_prompt,
        force_seed=args.force_seed,
        query_key=args.query,
        max_workers=args.parallel,
        cooldown=args.cooldown,
        max_research_iterations=args.max_research_iterations,
    )


def cmd_history(args: argparse.Namespace) -> None:
    from research_learner.db import LearnerDB

    db_path = Path(args.db) if args.db else None
    db = LearnerDB(db_path or Path(__file__).parent / "learner.db")

    iters = db.get_all_learner_iterations()
    if not iters:
        print("No learner iterations recorded yet.")
        db.close()
        return

    print(f"{'Iter':>5} {'Prompt':>7} {'Avg':>6} {'Min':>6} {'Max':>6} {'Timestamp':<20}")
    print("-" * 60)
    for it in iters:
        avg = f"{it.avg_score:.1f}" if it.avg_score is not None else "  -"
        mn = f"{it.min_score:.1f}" if it.min_score is not None else "  -"
        mx = f"{it.max_score:.1f}" if it.max_score is not None else "  -"
        ts = it.created_at[:19] if it.created_at else ""
        print(f"{it.iteration_number:>5} {it.prompt_id:>7} {avg:>6} {mn:>6} {mx:>6} {ts:<20}")

    best = db.get_best_prompt()
    if best:
        best_avg = db.get_avg_score_for_prompt(best.id)
        print(f"\nBest prompt: id={best.id} (gen {best.generation}), avg score={best_avg:.1f}")

    db.close()


def cmd_export_best(args: argparse.Namespace) -> None:
    import json

    from research_learner.db import LearnerDB

    db_path = Path(args.db) if args.db else None
    db = LearnerDB(db_path or Path(__file__).parent / "learner.db")

    best = db.get_best_prompt()
    if not best:
        print("No scored prompts in DB.")
        db.close()
        return

    best_avg = db.get_avg_score_for_prompt(best.id)
    out_path = Path(__file__).resolve().parent.parent / "researcher" / "planner_prompt.json"

    try:
        artifact = json.loads(best.prompt_text)
    except (json.JSONDecodeError, TypeError):
        artifact = None

    from researcher.planner_prompt import validate_artifact

    if validate_artifact(artifact):
        out_path.write_text(json.dumps(artifact, indent=2) + "\n")
        print(f"Exported JSON artifact id={best.id} (gen {best.generation}, avg score={best_avg:.1f})")
    else:
        out_path.write_text(best.prompt_text)
        print(
            f"WARNING: Best prompt is not a valid JSON artifact (pre-refactor prompt?).\n"
            f"Exported raw text id={best.id} (gen {best.generation}, avg score={best_avg:.1f})"
        )

    print(f"Written to: {out_path}")

    keys = list(artifact.keys()) if isinstance(artifact, dict) else []
    if keys:
        print(f"\nArtifact keys: {', '.join(keys)}")
        for k in keys:
            val = str(artifact[k])
            print(f"  {k}: {len(val)} chars")

    db.close()


def cmd_report(args: argparse.Namespace) -> None:
    from research_learner.db import LearnerDB
    from research_learner.report import generate_report

    db_path = Path(args.db) if args.db else None
    db = LearnerDB(db_path or Path(__file__).parent / "learner.db")

    report_path = generate_report(db)
    print(f"Report generated: {report_path}")
    db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="research_learner",
        description="Iteratively optimize the researcher's planner instruction.",
    )
    parser.add_argument("--db", help="Path to SQLite DB (default: learner.db)")

    sub = parser.add_subparsers(dest="command")

    # Default action: run the learner loop (also works without subcommand)
    parser.add_argument("--iterations", type=int, help="Max learner iterations")
    parser.add_argument("--hours", type=float, help="Max wall-clock hours to run")
    parser.add_argument("--seed-from-file", help="Path to a text file with the seed prompt")
    parser.add_argument("--force-seed", action="store_true", help="Insert seed even if DB has prompts")
    parser.add_argument("--query", help="Run against a single query key")
    parser.add_argument("--parallel", type=int, default=4, help="Thread pool size (default: 4)")
    parser.add_argument("--cooldown", type=float, default=2.0, help="Seconds between iterations (default: 2)")
    parser.add_argument("--max-research-iterations", type=int, default=5, help="Max planner iterations per research run")
    parser.add_argument("--history", action="store_true", help="Show score history")
    parser.add_argument("--export-best", action="store_true", help="Export best prompt to researcher/planner_prompt.json")
    parser.add_argument("--report", action="store_true", help="Generate HTML report")

    args = parser.parse_args()

    if args.history:
        cmd_history(args)
    elif args.export_best:
        cmd_export_best(args)
    elif args.report:
        cmd_report(args)
    elif args.iterations is not None or args.hours is not None:
        cmd_run(args)
    else:
        parser.print_help()
        print("\nExamples:")
        print("  python -m research_learner.run --hours 8 --seed-from-file my_prompt.txt")
        print("  python -m research_learner.run --iterations 20")
        print("  python -m research_learner.run --history")
        print("  python -m research_learner.run --report")
        print("  python -m research_learner.run --export-best")


if __name__ == "__main__":
    main()
