from __future__ import annotations

import argparse
import json

from prguard.flow import ReviewFlow
from prguard.models import PRReviewRequest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prguard", description="PRGuard multi-agent pull request review")
    subparsers = parser.add_subparsers(dest="command", required=True)
    review = subparsers.add_parser("review", help="review one GitHub pull request")
    target = review.add_mutually_exclusive_group(required=True)
    target.add_argument("--repo")
    target.add_argument("--url", dest="pr_url")
    review.add_argument("--pr", dest="pr_number", type=int)
    review.add_argument("--strategy", choices=("single", "static", "dynamic"), default="dynamic")
    review.add_argument("--model")
    review.add_argument("--output-dir", default="artifacts/review")
    review.add_argument("--concurrency", type=int, default=3)
    review.add_argument("--timeout-seconds", type=int, default=90)
    review.add_argument("--max-agent-calls", type=int, default=6)

    args = parser.parse_args(argv)
    if args.command == "review":
        if args.repo and not args.pr_number:
            parser.error("--repo requires --pr")
        request = PRReviewRequest(
            repo=args.repo,
            pr_number=args.pr_number,
            pr_url=args.pr_url,
            strategy=args.strategy,
            mode="online",
            model=args.model,
            output_dir=args.output_dir,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout_seconds,
            max_agent_calls=args.max_agent_calls,
        )
        flow = ReviewFlow()
        result = flow.kickoff(inputs={"request": request.model_dump()})
        print(json.dumps({
            "run_id": flow.state.run_id,
            "status": flow.state.review_status,
            "findings": len(flow.state.verified_findings),
            "output_dir": request.output_dir,
            "result": result,
        }, indent=2))
        return 0 if flow.state.review_status in {"completed", "partial"} else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
