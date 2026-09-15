import argparse
from .report import generate_report


def main():
    parser = argparse.ArgumentParser(description="读取已有求解JSON结果，生成离线HTML可视化")
    parser.add_argument("--input", required=True, help="包含solve_results.jsonl的结果目录")
    parser.add_argument("--output", required=True, help="新HTML文件路径")
    args = parser.parse_args()
    try:
        path = generate_report(args.input, args.output)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"ERROR: {exc}\n")
    print(path)


if __name__ == "__main__":
    main()
