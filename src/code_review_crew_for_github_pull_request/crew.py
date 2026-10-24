import os

from crewai import Agent, Crew, LLM, Process, Task
from crewai.project import CrewBase, agent, crew, task, before_kickoff
from dotenv import load_dotenv
from crewai_tools import ScrapeWebsiteTool
from code_review_crew_for_github_pull_request.tools.github_tools import (
    FetchPRFilesTool,
    FindRelatedFilesTool,
    FindRelatedPRsTool,
)

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# CrewAI loads .env without overriding, so a stale key exported in the shell would win.
# Make this project's .env authoritative for a reproducible local run.
load_dotenv(override=True)


def build_deepseek_llm(default_model: str) -> LLM:
    """Create the DeepSeek LLM shared by every agent, with env-based overrides."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is not set. Add it to your .env file before running the crew."
        )

    model = os.getenv("DEEPSEEK_MODEL") or default_model
    if "/" not in model:
        # LiteLLM routes providers by prefix, so accept both "deepseek-chat" and
        # "deepseek/deepseek-chat" from DEEPSEEK_MODEL.
        model = f"deepseek/{model}"

    return LLM(
        model=model,
        base_url=os.getenv("DEEPSEEK_API_BASE", DEFAULT_DEEPSEEK_BASE_URL),
        api_key=api_key,
    )


@CrewBase
class CodeReviewCrewForGithubPullRequestCrew():
    """CodeReviewCrewForGithubPullRequest crew"""

    @before_kickoff
    def load_files_changed(self, inputs):
        self.files_changed = FetchPRFilesTool().run(inputs['pr_patch_url'])
        inputs['files_changed'] = self.files_changed
        return inputs

    @agent
    def quick_review_assistant(self) -> Agent:
        return Agent(
            config=self.agents_config['quick_review_assistant'],
            llm=build_deepseek_llm(self.agents_config['quick_review_assistant']['llm']),
            tools=[
                ScrapeWebsiteTool(),
                FetchPRFilesTool(),
            ]
        )

    @agent
    def code_review_expert(self) -> Agent:
        return Agent(
            config=self.agents_config['code_review_expert'],
            llm=build_deepseek_llm(self.agents_config['code_review_expert']['llm']),
        )

    @task
    def analyze_code_changes_task(self) -> Task:
        return Task(
            config=self.tasks_config['analyze_code_changes_task'],
            tools=[]
        )

    @task
    def find_related_prs_task(self) -> Task:
        return Task(
            config=self.tasks_config['find_related_prs_task'],
            tools=[FindRelatedPRsTool()]
        )

    @task
    def analyze_related_files_task(self) -> Task:
        return Task(
            config=self.tasks_config['analyze_related_files_task'],
            tools=[FindRelatedFilesTool()]
        )

    @task
    def generate_review_comment_task(self) -> Task:
        return Task(
            config=self.tasks_config['generate_review_comment_task']
        )

    @crew
    def crew(self) -> Crew:
        """Creates the CodeReviewCrewForGithubPullRequest crew"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
