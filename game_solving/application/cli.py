"""Thin CLI; application and domain classes can be used without a command line."""

import logging
import argparse
import json
import sys
from pathlib import Path
from game_solving.infrastructure.config import load_config
from .pipeline import Pipeline


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="个人用户双向带宽模拟、博弈求解与证据标注"
    )
    parser.add_argument("command", choices=("generate", "solve", "run"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, default=Path("outputs/demo"))
    parser.add_argument("--input", type=Path)
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    )
    parser.add_argument(
        "--max-iterations",
        "--max-steps",
        dest="max_iterations",
        type=int,
        help="最大外层迭代轮数；--max-steps 为兼容别名",
    )
    parser.add_argument("--max-total-steps", type=int, help="所有阶段共享的工作量上限")
    parser.add_argument("--time-budget-ms", type=float)
    args = parser.parse_args(argv)
    overrides = {
        k: v
        for k, v in vars(args).items()
        if k in ("max_iterations", "max_total_steps", "time_budget_ms")
        and v is not None
    }
    try:
        config = load_config(args.config, {"solver": overrides} if overrides else None)
        if args.log_level:
            config["logging"]["level"] = args.log_level
        logging.basicConfig(
            level=config["logging"]["level"],
            format="%(asctime)s %(levelname)s %(name)s | %(message)s",
            datefmt="%H:%M:%S",
            stream=sys.stderr,
            force=True,
        )
        code, summary = Pipeline(config).execute(args.command, args.output, args.input)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return code
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2
