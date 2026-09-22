import argparse
from pathlib import Path
from .report import generate_report
from .grid_report import generate_grid_reports


def main():
    parser = argparse.ArgumentParser(description="读取已有求解JSON结果，生成离线HTML可视化")
    parser.add_argument("--input", required=True, help="包含solve_results.jsonl的结果目录")
    parser.add_argument("--output", required=True, help="输出文件或目录")
    parser.add_argument(
        "--mode",
        choices=("single", "grid", "auto"),
        default="auto",
        help="生成模式：single为旧版单HTML，grid为按人数拆分+数据解耦存储，auto自动判断",
    )
    args = parser.parse_args()
    try:
        out_path = Path(args.output)
        if args.mode == "grid" or (args.mode == "auto" and (not out_path.suffix or out_path.is_dir())):
            path = generate_grid_reports(args.input, args.output)
        else:
            path = generate_report(args.input, args.output)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"ERROR: {exc}\n")
    print(path)


if __name__ == "__main__":
    main()
