import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BioGen CLI")
    parser.add_argument("query", nargs="?", help="Natural language analysis request")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.query:
        print(f"Received query: {args.query}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
