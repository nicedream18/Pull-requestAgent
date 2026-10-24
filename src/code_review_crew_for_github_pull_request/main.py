#!/usr/bin/env python
import os
import sys
import warnings

from code_review_crew_for_github_pull_request.crew import CodeReviewCrewForGithubPullRequestCrew

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

# This main file is intended to be a way for your to run your
# crew locally, so refrain from adding unnecessary logic into this file.
# Replace with inputs you want to test with, it will automatically
# interpolate any tasks and agents information


def _review_inputs() -> dict[str, str]:
    repo_name = os.getenv("REVIEW_REPO")
    pr_patch_url = os.getenv("REVIEW_PR_PATCH_URL")
    if not repo_name or not pr_patch_url:
        raise RuntimeError(
            "Set REVIEW_REPO and REVIEW_PR_PATCH_URL for the retained upstream template entry point; "
            "use `prguard review --repo owner/repo --pr 123` for the PRGuard CLI."
        )
    return {"pr_patch_url": pr_patch_url, "repo_name": repo_name, "files_changed": ""}

def run():
    """
    Run the crew.
    """
    CodeReviewCrewForGithubPullRequestCrew().crew().kickoff(inputs=_review_inputs())


def train():
    """
    Train the crew for a given number of iterations.
    """
    try:
        CodeReviewCrewForGithubPullRequestCrew().crew().train(n_iterations=int(sys.argv[1]), filename=sys.argv[2], inputs=_review_inputs())

    except Exception as e:
        raise Exception(f"An error occurred while training the crew: {e}")

def replay():
    """
    Replay the crew execution from a specific task.
    """
    try:
        CodeReviewCrewForGithubPullRequestCrew().crew().replay(task_id=sys.argv[1])

    except Exception as e:
        raise Exception(f"An error occurred while replaying the crew: {e}")

def test():
    """
    Test the crew execution and returns the results.
    """
    try:
        model_name = (
            sys.argv[2]
            if len(sys.argv) > 2
            else os.getenv("DEEPSEEK_MODEL", "deepseek/deepseek-chat")
        )
        CodeReviewCrewForGithubPullRequestCrew().crew().test(n_iterations=int(sys.argv[1]), openai_model_name=model_name, inputs=_review_inputs())

    except Exception as e:
        raise Exception(f"An error occurred while testing the crew: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: main.py <command> [<args>]")
        sys.exit(1)

    command = sys.argv[1]
    if command == "run":
        run()
    elif command == "train":
        train()
    elif command == "replay":
        replay()
    elif command == "test":
        test()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
