"""Build `prguard_v2.json`, the independent multi-file candidate benchmark.

Every label here is `draft`. None has been reviewed by a second human, and no label was
produced by a model. The set exists so the pipeline can be exercised on multi-file PRs
with several defects per case, which `prguard_v1.json` cannot express.

Run:

    python evals/datasets/build_prguard_v2.py

Design notes that matter for scoring:

* `has_defect` is explicit, so a clean PR is distinguishable from an unfinished label.
* `defects: []` with `has_defect: false` is a clean PR.
* Every defect carries `line_range` for the file it belongs to, so path and line can be
  scored independently.
* Cases whose label the author considers arguable say so in `label_notes` and are marked
  `label_review_status: "disputed"`.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "prguard_v2.json"

DRAFT = "draft"
DISPUTED = "disputed"


def defect(
    defect_id: str,
    issue_type: str,
    file_path: str,
    line_range: list[int],
    *,
    severity: str = "high",
    category: str = "bug",
    description: str,
    trigger_condition: str,
    supporting_evidence: str,
    expected_behavior: str = "The unsafe value is validated or contained before use.",
    actual_behavior: str | None = None,
) -> dict:
    return {
        "defect_id": defect_id,
        "issue_type": issue_type,
        "defect_type": issue_type,
        "category": category,
        "severity": severity,
        "file_path": file_path,
        "line_range": line_range,
        "description": description,
        "trigger_condition": trigger_condition,
        "supporting_evidence": supporting_evidence,
        "expected_behavior": expected_behavior,
        "actual_behavior": actual_behavior or description,
        "label_review_status": DRAFT,
    }


def case(
    case_id: str,
    pr_title: str,
    split: str,
    files: list[dict],
    defects: list[dict],
    *,
    has_defect: bool | None = None,
    status: str = DRAFT,
    notes: str = "",
) -> dict:
    return {
        "case_id": case_id,
        "pr_title": pr_title,
        "split": split,
        "files": files,
        "defects": defects,
        "has_defect": bool(defects) if has_defect is None else has_defect,
        "label_review_status": status,
        "label_notes": notes,
    }


def f(path: str, code: str, status: str = "modified") -> dict:
    return {"path": path, "code": code, "status": status}


CASES: list[dict] = [
    # ---------------------------------------------------------------- multi-file, one defect
    case(
        "mf-secret-01",
        "feat: load billing credentials from config",
        "dev",
        [
            f("app/config.py", 'API_TOKEN = "sk-live-8f2b41c9de77"\n\nTIMEOUT = 30\n'),
            f("app/billing.py", "from app.config import API_TOKEN\n\n\ndef headers():\n    return {'Authorization': API_TOKEN}\n"),
        ],
        [
            defect(
                "mf-secret-01-d1",
                "hardcoded_secret",
                "app/config.py",
                [1, 1],
                severity="critical",
                category="security",
                description="A live-looking API token is committed in app/config.py.",
                trigger_condition="Anyone with repository read access reads app/config.py line 1.",
                supporting_evidence='API_TOKEN = "sk-live-8f2b41c9de77"',
                expected_behavior="The token is read from the environment or a secret store.",
            )
        ],
        notes="Second file exists to make a wrong path plausible; the defect is only in config.py.",
    ),
    case(
        "mf-cross-02",
        "refactor: rename Account.balance to Account.amount",
        "holdout",
        [
            f(
                "app/models.py",
                "class Account:\n    def __init__(self, amount: int = 0) -> None:\n        self.amount = amount\n",
            ),
            f(
                "app/report.py",
                "from app.models import Account\n\n\ndef total(account: Account) -> int:\n    return account.balance\n",
            ),
        ],
        [
            defect(
                "mf-cross-02-d1",
                "cross_file_breakage",
                "app/report.py",
                [5, 5],
                severity="high",
                description="report.total still reads Account.balance after the field was renamed to amount.",
                trigger_condition="Calling total() with any Account instance raises AttributeError.",
                supporting_evidence="return account.balance",
                expected_behavior="report.total reads account.amount, matching the renamed attribute.",
            )
        ],
        notes="Cross-file: the breaking change is in models.py but the failing line is in report.py.",
    ),
    case(
        "mf-two-defects-03",
        "feat: add search and upload helpers",
        "test",
        [
            f(
                "app/search.py",
                "def search(cursor, name):\n    cursor.execute(f\"SELECT * FROM users WHERE name = '{name}'\")\n",
            ),
            f("app/uploads.py", "import os\n\n\ndef save(path):\n    os.chmod(path, 0o777)\n"),
            f("app/util.py", "def noop():\n    return None\n"),
        ],
        [
            defect(
                "mf-two-defects-03-d1",
                "sql_injection",
                "app/search.py",
                [2, 2],
                severity="critical",
                category="security",
                description="The name parameter is interpolated into the query string.",
                trigger_condition="A name containing a quote changes the query structure.",
                supporting_evidence='cursor.execute(f"SELECT * FROM users WHERE name = \'{name}\'")',
            ),
            defect(
                "mf-two-defects-03-d2",
                "overly_permissive_file_mode",
                "app/uploads.py",
                [5, 5],
                severity="high",
                category="security",
                description="The uploaded file is made world-writable.",
                trigger_condition="save() is called on any path.",
                supporting_evidence="os.chmod(path, 0o777)",
                expected_behavior="Only the owner should be able to write the file.",
            ),
        ],
        notes="Two defects in two files, plus a third file with nothing wrong.",
    ),
    # ---------------------------------------------------------------- same basename, two dirs
    case(
        "mf-basename-04",
        "feat: add test settings and app settings",
        "test",
        [
            f("app/settings.py", 'TOKEN = "sk-live-1a2b3c4d5e6f"\n'),
            f("tests/settings.py", "DEBUG = True\n"),
        ],
        [
            defect(
                "mf-basename-04-d1",
                "hardcoded_secret",
                "app/settings.py",
                [1, 1],
                severity="critical",
                category="security",
                description="The application settings file embeds a credential.",
                trigger_condition="Reading app/settings.py exposes the token.",
                supporting_evidence='TOKEN = "sk-live-1a2b3c4d5e6f"',
            )
        ],
        notes="A basename-only path (settings.py) must be ambiguous here, not resolved to the first match.",
    ),
    case(
        "mf-basename-05",
        "test: move debug flag into test settings",
        "test",
        [
            f("app/settings.py", "DEBUG = False\n"),
            f("tests/settings.py", 'TOKEN = "sk-live-9z8y7x6w5v4u"\n'),
        ],
        [
            defect(
                "mf-basename-05-d1",
                "hardcoded_secret",
                "tests/settings.py",
                [1, 1],
                severity="critical",
                category="security",
                description="A credential is committed in the test settings module.",
                trigger_condition="Reading tests/settings.py exposes the token.",
                supporting_evidence='TOKEN = "sk-live-9z8y7x6w5v4u"',
            )
        ],
        notes="Mirror of 04: the duplicate basename that matters is the test one.",
    ),
    # ---------------------------------------------------------------- clean multi-file PRs
    case(
        "mf-clean-06",
        "refactor: split formatting helpers",
        "dev",
        [
            f("app/format.py", "def display_name(first, last):\n    return f\"{first.strip()} {last.strip()}\"\n"),
            f("app/strings.py", "def repeat(text, times):\n    return text * times\n"),
            f("app/numbers.py", "def clamp(value, low, high):\n    return min(max(value, low), high)\n"),
        ],
        [],
        has_defect=False,
        notes="Nothing is wrong; any finding here is a false positive.",
    ),
    case(
        "mf-clean-07",
        "docs: document the review CLI",
        "dev",
        [f("docs/usage.md", "# Usage\n\nRun `prguard review --repo owner/repo --pr 12`.\n")],
        [],
        has_defect=False,
        notes="Documentation-only change.",
    ),
    # ---------------------------------------------------------------- repeated similar evidence
    case(
        "mf-similar-08",
        "feat: add two readers",
        "test",
        [
            f("app/reader_a.py", "def read_a(handle):\n    return handle.read()\n"),
            f("app/reader_b.py", "def read_b(handle):\n    return handle.read()\n"),
            f("app/open_a.py", "def open_a(path):\n    handle = open(path)\n    return handle.read()\n"),
        ],
        [
            defect(
                "mf-similar-08-d1",
                "resource_leak",
                "app/open_a.py",
                [2, 2],
                severity="medium",
                description="The handle opened in open_a is never closed.",
                trigger_condition="open_a() returns and the file object is garbage collected.",
                supporting_evidence="handle = open(path)",
                expected_behavior="Use a with-block so the handle closes on every path.",
            )
        ],
        notes="`handle.read()` appears in three files; only open_a actually opens a handle.",
    ),
    # ---------------------------------------------------------------- three defects
    case(
        "mf-three-defects-09",
        "feat: batch import pipeline",
        "test",
        [
            f("app/import_a.py", "def parse(text):\n    import json\n    return json.loads(text)\n"),
            f("app/import_b.py", "def ratio(total, count):\n    return total / count\n"),
            f("app/import_c.py", "def last(items):\n    for i in range(len(items) + 1):\n        print(items[i])\n"),
        ],
        [
            defect(
                "mf-three-defects-09-d1",
                "missing_exception_handling",
                "app/import_a.py",
                [3, 3],
                severity="medium",
                description="json.loads raises on malformed input and nothing handles it.",
                trigger_condition="parse() receives invalid JSON.",
                supporting_evidence="return json.loads(text)",
            ),
            defect(
                "mf-three-defects-09-d2",
                "division_by_zero",
                "app/import_b.py",
                [2, 2],
                severity="medium",
                description="count is used as a divisor with no zero guard.",
                trigger_condition="ratio() is called with count == 0.",
                supporting_evidence="return total / count",
            ),
            defect(
                "mf-three-defects-09-d3",
                "boundary_condition",
                "app/import_c.py",
                [2, 2],
                severity="high",
                description="range(len(items) + 1) indexes one past the end.",
                trigger_condition="last() is called with any list; the final iteration raises IndexError.",
                supporting_evidence="for i in range(len(items) + 1):",
            ),
        ],
        notes="Three independent defects; a single-defect metric would understate recall.",
    ),
    # ---------------------------------------------------------------- same line, two defects
    case(
        "mf-same-line-10",
        "feat: persist upload metadata",
        "test",
        [
            f(
                "app/persist.py",
                "import os\n\n\ndef persist(path):\n    os.chmod(path, 0o777); TOKEN = \"sk-live-77aa88bb99cc\"\n",
            )
        ],
        [
            defect(
                "mf-same-line-10-d1",
                "overly_permissive_file_mode",
                "app/persist.py",
                [5, 5],
                severity="high",
                category="security",
                description="The file is made world-writable.",
                trigger_condition="persist() is called on any path.",
                supporting_evidence="os.chmod(path, 0o777)",
            ),
            defect(
                "mf-same-line-10-d2",
                "hardcoded_secret",
                "app/persist.py",
                [5, 5],
                severity="critical",
                category="security",
                description="A credential literal is assigned on the same line.",
                trigger_condition="Reading app/persist.py exposes the token.",
                supporting_evidence='TOKEN = "sk-live-77aa88bb99cc"',
            ),
        ],
        notes="Two distinct defects on one physical line; dedup must not merge them.",
    ),
    # ---------------------------------------------------------------- broken windows / encoding
    case(
        "mf-deleted-11",
        "chore: drop the legacy exporter",
        "test",
        [
            f("app/exporter.py", "def export(path):\n    return open(path).read()\n", status="removed"),
            f("app/new_export.py", "def export(path):\n    with open(path) as handle:\n        return handle.read()\n"),
        ],
        [],
        has_defect=False,
        notes="Removed file plus a correct replacement; a finding on the removed path is wrong.",
    ),
    # ---------------------------------------------------------------- single-file defects
    case(
        "sf-null-12",
        "feat: add display_name",
        "holdout",
        [f("app/profile.py", "from typing import Optional\n\n\ndef display_name(user: Optional[object]) -> str:\n    return user.name\n")],
        [
            defect(
                "sf-null-12-d1",
                "null_dereference",
                "app/profile.py",
                [5, 5],
                severity="high",
                description="The Optional user is dereferenced without a None check.",
                trigger_condition="display_name(None) is called, which the annotation permits.",
                supporting_evidence="return user.name",
            )
        ],
    ),
    case(
        "sf-leak-13",
        "feat: add export helper",
        "dev",
        [f("app/export.py", "def export(path):\n    handle = open(path)\n    return handle.read()\n")],
        [
            defect(
                "sf-leak-13-d1",
                "resource_leak",
                "app/export.py",
                [2, 2],
                severity="medium",
                description="The file handle is never closed.",
                trigger_condition="export() returns or raises before closing.",
                supporting_evidence="handle = open(path)",
            )
        ],
    ),
    case(
        "sf-testgap-14",
        "feat: transfer funds between accounts",
        "test",
        [
            f(
                "app/transfers.py",
                "def transfer_funds(source, target, amount):\n    if amount <= 0:\n        raise ValueError('amount must be positive')\n    source.debit(amount)\n    target.credit(amount)\n",
            )
        ],
        [
            defect(
                "sf-testgap-14-d1",
                "missing_test_coverage",
                "app/transfers.py",
                [4, 5],
                severity="medium",
                category="testing",
                description="The debit/credit pair has no test for a partial failure.",
                trigger_condition="credit() fails after debit() succeeded.",
                supporting_evidence="target.credit(amount)",
                expected_behavior="A test covers the credit-failure path and any rollback.",
            )
        ],
        notes="A diff cannot prove the absence of a test; expect UNKNOWN unless a test file is supplied.",
    ),
    # ---------------------------------------------------------------- safe counterparts
    case(
        "safe-param-sql-15",
        "fix: parameterize the user lookup",
        "dev",
        [f("app/search.py", "def search(cursor, name):\n    cursor.execute('SELECT * FROM users WHERE name = %s', (name,))\n")],
        [],
        has_defect=False,
        notes="Parameterized query; any sql_injection finding is a false positive.",
    ),
    case(
        "safe-subprocess-16",
        "fix: run the tool without a shell",
        "dev",
        [
            f(
                "app/runner.py",
                "import subprocess\n\n\ndef run(program, args):\n    return subprocess.run([program, *args], check=True, capture_output=True)\n",
            )
        ],
        [],
        has_defect=False,
        notes="Argument vector, no shell. A missing_exception_handling claim on line 5 is arguable; see the arbitration report.",
        status=DISPUTED,
    ),
    case(
        "safe-with-open-17",
        "refactor: use a context manager",
        "dev",
        [f("app/read.py", "def read(path):\n    with open(path) as handle:\n        return handle.read()\n")],
        [],
        has_defect=False,
    ),
    case(
        "safe-guard-18",
        "fix: guard the Optional user",
        "dev",
        [
            f(
                "app/profile.py",
                "from typing import Optional\n\n\ndef display_name(user: Optional[object]) -> str:\n    if user is None:\n        return ''\n    return user.name\n",
            )
        ],
        [],
        has_defect=False,
    ),
    case(
        "safe-env-secret-19",
        "fix: read the token from the environment",
        "dev",
        [f("app/config.py", "import os\n\nAPI_TOKEN = os.getenv('API_TOKEN')\n")],
        [],
        has_defect=False,
        notes="Secret-named variable, but it is loaded from the environment.",
    ),
    # ---------------------------------------------------------------- tricky / adversarial
    case(
        "tricky-constant-sql-20",
        "feat: count all users",
        "test",
        [f("app/count.py", "def count(cursor):\n    cursor.execute('SELECT COUNT(*) FROM users')\n    return cursor.fetchone()\n")],
        [],
        has_defect=False,
        notes="A query call with no interpolation point; an injection claim must be refuted.",
    ),
    case(
        "tricky-mixed-sql-21",
        "feat: add safe and unsafe lookups",
        "test",
        [
            f(
                "app/search.py",
                "def search(cursor, name, mode):\n"
                "    if mode == 'safe':\n"
                "        cursor.execute('SELECT * FROM users WHERE name = %s', (name,))\n"
                "    else:\n"
                "        cursor.execute(f'SELECT * FROM users WHERE name = {name}')\n",
            )
        ],
        [
            defect(
                "tricky-mixed-sql-21-d1",
                "sql_injection",
                "app/search.py",
                [5, 5],
                severity="critical",
                category="security",
                description="The else branch interpolates name into the query.",
                trigger_condition="mode is anything other than 'safe'.",
                supporting_evidence="cursor.execute(f'SELECT * FROM users WHERE name = {name}')",
            )
        ],
        notes="One function holds a safe and an unsafe call. A finding on line 3 is wrong; on line 5 is right.",
    ),
    case(
        "tricky-guard-not-dominating-22",
        "feat: print the last item",
        "test",
        [
            f(
                "app/batch.py",
                "def last(items):\n"
                "    for i in range(len(items) + 1):\n"
                "        print(items[i])\n"
                "        if i >= len(items):\n"
                "            break\n",
            )
        ],
        [
            defect(
                "tricky-guard-not-dominating-22-d1",
                "boundary_condition",
                "app/batch.py",
                [2, 2],
                severity="high",
                description="The bounds check runs after the read, so it cannot prevent the IndexError.",
                trigger_condition="last() is called with any list; the final iteration reads items[len(items)] first.",
                supporting_evidence="for i in range(len(items) + 1):",
            )
        ],
        notes="The guard exists but does not dominate the read.",
    ),
    case(
        "tricky-guard-dominating-23",
        "feat: print the last item safely",
        "test",
        [
            f(
                "app/batch.py",
                "def last(items):\n"
                "    for i in range(len(items) + 1):\n"
                "        if i >= len(items):\n"
                "            break\n"
                "        print(items[i])\n",
            )
        ],
        [],
        has_defect=False,
        notes="Mirror of 22 with the guard before the read; a boundary claim must be refuted.",
    ),
    case(
        "tricky-evidence-no-defect-24",
        "feat: document the injection risk",
        "test",
        [f("app/notes.py", "SQL_INJECTION_NOTE = 'do not interpolate user input into a query'\n")],
        [],
        has_defect=False,
        notes="The words appear in a constant; text matching must not become proof.",
    ),
    case(
        "tricky-rename-both-sides-25",
        "refactor: rename User.name to User.full_name",
        "test",
        [
            f("app/models.py", "class User:\n    def __init__(self, full_name: str) -> None:\n        self.full_name = full_name\n"),
            f("app/views.py", "def label(user):\n    return user.full_name\n"),
        ],
        [],
        has_defect=False,
        notes="Both sides renamed; a cross_file_breakage claim here is a false positive.",
    ),
    case(
        "tricky-two-agents-one-defect-26",
        "feat: add chmod helper",
        "holdout",
        [f("app/uploads.py", "import os\n\n\ndef save(path):\n    os.chmod(path, 0o777)\n")],
        [
            defect(
                "tricky-two-agents-one-defect-26-d1",
                "overly_permissive_file_mode",
                "app/uploads.py",
                [5, 5],
                severity="high",
                category="security",
                description="save() makes the file writable by everyone.",
                trigger_condition="save() is called on any path.",
                supporting_evidence="os.chmod(path, 0o777)",
            )
        ],
        notes="Two agents will report this with different wording; it must count once.",
    ),
    case(
        "tricky-adjacent-same-type-27",
        "feat: write two files",
        "holdout",
        [
            f(
                "app/writer.py",
                "import os\n\n\ndef write_a(path):\n    os.chmod(path, 0o777)\n\n\ndef write_b(path):\n    os.chmod(path, 0o777)\n",
            )
        ],
        [
            defect(
                "tricky-adjacent-same-type-27-d1",
                "overly_permissive_file_mode",
                "app/writer.py",
                [5, 5],
                severity="high",
                category="security",
                description="write_a makes the file world-writable.",
                trigger_condition="write_a() is called on any path.",
                supporting_evidence="os.chmod(path, 0o777)",
            ),
            defect(
                "tricky-adjacent-same-type-27-d2",
                "overly_permissive_file_mode",
                "app/writer.py",
                [9, 9],
                severity="high",
                category="security",
                description="write_b makes the file world-writable.",
                trigger_condition="write_b() is called on any path.",
                supporting_evidence="os.chmod(path, 0o777)",
            ),
        ],
        notes="Identical evidence, same type, four lines apart: two real defects that must NOT be merged.",
    ),
    case(
        "tricky-multiline-sql-28",
        "feat: build a filtered query",
        "test",
        [
            f(
                "app/report.py",
                "def report(cursor, table):\n    query = 'SELECT * FROM ' + table\n    cursor.execute(query)\n",
            )
        ],
        [
            defect(
                "tricky-multiline-sql-28-d1",
                "sql_injection",
                "app/report.py",
                [2, 2],
                severity="critical",
                category="security",
                description="table is concatenated into the query string.",
                trigger_condition="table comes from user input.",
                supporting_evidence="query = 'SELECT * FROM ' + table",
                expected_behavior="Validate the table against an allow-list; identifiers cannot be bound.",
            )
        ],
        status=DISPUTED,
        notes="Interprocedural: the sink is on line 3 but the concatenation is on line 2. Layer 2 may answer UNKNOWN.",
    ),
    case(
        "tricky-late-import-29",
        "feat: parse queued payloads",
        "dev",
        [f("app/queue.py", "def parse(text):\n    import json\n    return json.loads(text)\n")],
        [
            defect(
                "tricky-late-import-29-d1",
                "missing_exception_handling",
                "app/queue.py",
                [3, 3],
                severity="medium",
                description="json.loads raises ValueError on malformed input and nothing handles it.",
                trigger_condition="parse() receives a non-JSON payload.",
                supporting_evidence="return json.loads(text)",
            )
        ],
    ),
    case(
        "tricky-docstring-only-30",
        "docs: expand the module docstring",
        "dev",
        [f("app/module.py", '"""Helpers for the review pipeline.\n\nNo behaviour change."""\n\nVERSION = "1.2"\n')],
        [],
        has_defect=False,
    ),
    case(
        "tricky-deleted-target-31",
        "chore: remove an unused helper",
        "holdout",
        [
            f("app/old.py", "def old_helper():\n    return 1\n", status="removed"),
            f("app/current.py", "def current_helper():\n    return 2\n"),
        ],
        [],
        has_defect=False,
        notes="A finding citing app/old.py must not be recovered onto app/current.py.",
    ),
    case(
        "tricky-windows-path-32",
        "feat: add an export path helper",
        "holdout",
        [
            f("app/paths.py", "def export_path(root):\n    return root + '/out.csv'\n"),
            f("app/export.py", "def export(root):\n    handle = open(root + '/out.csv')\n    return handle.read()\n"),
        ],
        [
            defect(
                "tricky-windows-path-32-d1",
                "resource_leak",
                "app/export.py",
                [2, 2],
                severity="medium",
                description="The handle opened in export is never closed.",
                trigger_condition="export() returns before closing.",
                supporting_evidence="handle = open(root + '/out.csv')",
            )
        ],
        notes="Two files with similar names; a Windows-style prediction (app\\\\export.py) must normalize here.",
    ),
]


def main() -> int:
    splits = {"dev": 0, "test": 0, "holdout": 0}
    for item in CASES:
        splits[item["split"]] += 1
    multi = sum(1 for item in CASES if len(item["files"]) > 1)
    clean = sum(1 for item in CASES if not item["has_defect"])
    multi_defect = sum(1 for item in CASES if len(item["defects"]) > 1)

    document = {
        "dataset_version": "2.0-candidate",
        "description": (
            "Independent multi-file candidate benchmark. Labels are drafts written by the "
            "author of the code; none is human-reviewed and none was produced by a model. "
            "Do not publish generalisation claims from this set until label_review_status "
            "becomes human_reviewed for every case."
        ),
        "label_policy": {
            "allowed_status": [DRAFT, "human_reviewed", "indeterminate"],
            "current": "all cases are draft",
            "human_reviewed_count": 0,
            "llm_generated_labels": False,
        },
        "stats": {
            "case_count": len(CASES),
            "multi_file_cases": multi,
            "clean_cases": clean,
            "multi_defect_cases": multi_defect,
            "labelled_defects": sum(len(item["defects"]) for item in CASES),
            "splits": splits,
        },
        "cases": CASES,
    }
    OUT.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(document["stats"], indent=2))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
