# Candidate label review packet

Dataset: `2.1-candidate`, 36 case(s)

**Every label below is `draft`.** This packet is input for a human reviewer. It was
produced mechanically from the fixture code; it is not a second opinion and it does not
raise any label's status. Allowed statuses: `draft`, `reviewed`, `needs_revision`, `indeterminate`.

For each case, decide:

1. Is the labelled defect real, and is the severity defensible?
2. Is the line range the smallest accurate range?
3. Is there any *other* real defect that is not labelled? (This is the main risk for
   the clean cases.)
4. Is `indeterminate` the honest answer because the code alone cannot decide?

Record decisions in `candidates.csv`; do not edit `prguard_v2.json` directly.

---

## mf-secret-01

**PR title:** feat: load billing credentials from config

**Split:** `dev` | **Label status:** `draft` | **Labelled defects:** 1 | **Files:** 2

### `app/config.py` (modified)

**Content (no base revision in this case):**

```python
API_TOKEN = "sk-live-8f2b41c9de77"

TIMEOUT = 30
```
### `app/billing.py` (modified)

**Content (no base revision in this case):**

```python
from app.config import API_TOKEN


def headers():
    return {'Authorization': API_TOKEN}
```
### Proposed ground truth

#### `mf-secret-01-d1`

| Field | Value |
| --- | --- |
| issue_type | `hardcoded_secret` |
| severity | `critical` |
| file | `app/config.py` |
| line_range | 1-1 |
| evidence line in file | 1 |
| deterministic check | supported: API_TOKEN is assigned a credential-like literal |

- **Description:** A live-looking API token is committed in app/config.py.
- **Trigger condition:** Anyone with repository read access reads app/config.py line 1.
- **Expected:** The token is read from the environment or a secret store.
- **Actual:** A live-looking API token is committed in app/config.py.
- **Evidence:** `API_TOKEN = "sk-live-8f2b41c9de77"`

### Author notes

Second file exists to make a wrong path plausible; the defect is only in config.py.

---

## mf-cross-02

**PR title:** refactor: rename Account.balance to Account.amount

**Split:** `holdout` | **Label status:** `draft` | **Labelled defects:** 1 | **Files:** 2

### `app/models.py` (modified)

**Content (no base revision in this case):**

```python
class Account:
    def __init__(self, amount: int = 0) -> None:
        self.amount = amount
```
### `app/report.py` (modified)

**Content (no base revision in this case):**

```python
from app.models import Account


def total(account: Account) -> int:
    return account.balance
```
### Proposed ground truth

#### `mf-cross-02-d1`

| Field | Value |
| --- | --- |
| issue_type | `cross_file_breakage` |
| severity | `high` |
| file | `app/report.py` |
| line_range | 5-5 |
| evidence line in file | 5 |
| deterministic check | UNKNOWN: no deterministic condition is registered for this issue type |

- **Description:** report.total still reads Account.balance after the field was renamed to amount.
- **Trigger condition:** Calling total() with any Account instance raises AttributeError.
- **Expected:** report.total reads account.amount, matching the renamed attribute.
- **Actual:** report.total still reads Account.balance after the field was renamed to amount.
- **Evidence:** `return account.balance`

### Author notes

Cross-file: the breaking change is in models.py but the failing line is in report.py.

---

## mf-two-defects-03

**PR title:** feat: add search and upload helpers

**Split:** `test` | **Label status:** `draft` | **Labelled defects:** 2 | **Files:** 3

### `app/search.py` (modified)

**Content (no base revision in this case):**

```python
def search(cursor, name):
    cursor.execute(f"SELECT * FROM users WHERE name = '{name}'")
```
### `app/uploads.py` (modified)

**Content (no base revision in this case):**

```python
import os


def save(path):
    os.chmod(path, 0o777)
```
### `app/util.py` (modified)

**Content (no base revision in this case):**

```python
def noop():
    return None
```
### Proposed ground truth

#### `mf-two-defects-03-d1`

| Field | Value |
| --- | --- |
| issue_type | `sql_injection` |
| severity | `critical` |
| file | `app/search.py` |
| line_range | 2-2 |
| evidence line in file | 2 |
| deterministic check | supported: query string is built by interpolation |

- **Description:** The name parameter is interpolated into the query string.
- **Trigger condition:** A name containing a quote changes the query structure.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** The name parameter is interpolated into the query string.
- **Evidence:** `cursor.execute(f"SELECT * FROM users WHERE name = '{name}'")`

#### `mf-two-defects-03-d2`

| Field | Value |
| --- | --- |
| issue_type | `overly_permissive_file_mode` |
| severity | `high` |
| file | `app/uploads.py` |
| line_range | 5-5 |
| evidence line in file | 5 |
| deterministic check | supported: mode 0o777 grants write access to other users |

- **Description:** The uploaded file is made world-writable.
- **Trigger condition:** save() is called on any path.
- **Expected:** Only the owner should be able to write the file.
- **Actual:** The uploaded file is made world-writable.
- **Evidence:** `os.chmod(path, 0o777)`

### Author notes

Two defects in two files, plus a third file with nothing wrong.

---

## mf-basename-04

**PR title:** feat: add test settings and app settings

**Split:** `test` | **Label status:** `draft` | **Labelled defects:** 1 | **Files:** 2

### `app/settings.py` (modified)

**Content (no base revision in this case):**

```python
TOKEN = "sk-live-1a2b3c4d5e6f"
```
### `tests/settings.py` (modified)

**Content (no base revision in this case):**

```python
DEBUG = True
```
### Proposed ground truth

#### `mf-basename-04-d1`

| Field | Value |
| --- | --- |
| issue_type | `hardcoded_secret` |
| severity | `critical` |
| file | `app/settings.py` |
| line_range | 1-1 |
| evidence line in file | 1 |
| deterministic check | supported: TOKEN is assigned a credential-like literal |

- **Description:** The application settings file embeds a credential.
- **Trigger condition:** Reading app/settings.py exposes the token.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** The application settings file embeds a credential.
- **Evidence:** `TOKEN = "sk-live-1a2b3c4d5e6f"`

### Author notes

A basename-only path (settings.py) must be ambiguous here, not resolved to the first match.

---

## mf-basename-05

**PR title:** test: move debug flag into test settings

**Split:** `test` | **Label status:** `draft` | **Labelled defects:** 1 | **Files:** 2

### `app/settings.py` (modified)

**Content (no base revision in this case):**

```python
DEBUG = False
```
### `tests/settings.py` (modified)

**Content (no base revision in this case):**

```python
TOKEN = "sk-live-9z8y7x6w5v4u"
```
### Proposed ground truth

#### `mf-basename-05-d1`

| Field | Value |
| --- | --- |
| issue_type | `hardcoded_secret` |
| severity | `critical` |
| file | `tests/settings.py` |
| line_range | 1-1 |
| evidence line in file | 1 |
| deterministic check | supported: TOKEN is assigned a credential-like literal |

- **Description:** A credential is committed in the test settings module.
- **Trigger condition:** Reading tests/settings.py exposes the token.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** A credential is committed in the test settings module.
- **Evidence:** `TOKEN = "sk-live-9z8y7x6w5v4u"`

### Author notes

Mirror of 04: the duplicate basename that matters is the test one.

---

## mf-clean-06

**PR title:** refactor: split formatting helpers

**Split:** `dev` | **Label status:** `draft` | **Labelled defects:** 0 | **Files:** 3

**No defect is labelled.** Confirm the change is genuinely clean.

### `app/format.py` (modified)

**Content (no base revision in this case):**

```python
def display_name(first, last):
    return f"{first.strip()} {last.strip()}"
```
### `app/strings.py` (modified)

**Content (no base revision in this case):**

```python
def repeat(text, times):
    return text * times
```
### `app/numbers.py` (modified)

**Content (no base revision in this case):**

```python
def clamp(value, low, high):
    return min(max(value, low), high)
```
### Author notes

Nothing is wrong; any finding here is a false positive.

---

## mf-clean-07

**PR title:** docs: document the review CLI

**Split:** `dev` | **Label status:** `draft` | **Labelled defects:** 0 | **Files:** 1

**No defect is labelled.** Confirm the change is genuinely clean.

### `docs/usage.md` (modified)

**Content (no base revision in this case):**

```python
# Usage

Run `prguard review --repo owner/repo --pr 12`.
```
### Author notes

Documentation-only change.

---

## mf-similar-08

**PR title:** feat: add two readers

**Split:** `test` | **Label status:** `draft` | **Labelled defects:** 1 | **Files:** 3

### `app/reader_a.py` (modified)

**Content (no base revision in this case):**

```python
def read_a(handle):
    return handle.read()
```
### `app/reader_b.py` (modified)

**Content (no base revision in this case):**

```python
def read_b(handle):
    return handle.read()
```
### `app/open_a.py` (modified)

**Content (no base revision in this case):**

```python
def open_a(path):
    handle = open(path)
    return handle.read()
```
### Proposed ground truth

#### `mf-similar-08-d1`

| Field | Value |
| --- | --- |
| issue_type | `resource_leak` |
| severity | `medium` |
| file | `app/open_a.py` |
| line_range | 2-2 |
| evidence line in file | 2 |
| deterministic check | supported: the reported open() call is outside a with-block and never closed |

- **Description:** The handle opened in open_a is never closed.
- **Trigger condition:** open_a() returns and the file object is garbage collected.
- **Expected:** Use a with-block so the handle closes on every path.
- **Actual:** The handle opened in open_a is never closed.
- **Evidence:** `handle = open(path)`

### Author notes

`handle.read()` appears in three files; only open_a actually opens a handle.

---

## mf-three-defects-09

**PR title:** feat: batch import pipeline

**Split:** `test` | **Label status:** `draft` | **Labelled defects:** 3 | **Files:** 3

### `app/import_a.py` (modified)

**Content (no base revision in this case):**

```python
def parse(text):
    import json
    return json.loads(text)
```
### `app/import_b.py` (modified)

**Content (no base revision in this case):**

```python
def ratio(total, count):
    return total / count
```
### `app/import_c.py` (modified)

**Content (no base revision in this case):**

```python
def last(items):
    for i in range(len(items) + 1):
        print(items[i])
```
### Proposed ground truth

#### `mf-three-defects-09-d1`

| Field | Value |
| --- | --- |
| issue_type | `missing_exception_handling` |
| severity | `medium` |
| file | `app/import_a.py` |
| line_range | 3-3 |
| evidence line in file | 3 |
| deterministic check | supported: json.loads escapes without a handler at this location |

- **Description:** json.loads raises on malformed input and nothing handles it.
- **Trigger condition:** parse() receives invalid JSON.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** json.loads raises on malformed input and nothing handles it.
- **Evidence:** `return json.loads(text)`

#### `mf-three-defects-09-d2`

| Field | Value |
| --- | --- |
| issue_type | `division_by_zero` |
| severity | `medium` |
| file | `app/import_b.py` |
| line_range | 2-2 |
| evidence line in file | 2 |
| deterministic check | supported: divisor 'count' is a parameter with no zero guard |

- **Description:** count is used as a divisor with no zero guard.
- **Trigger condition:** ratio() is called with count == 0.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** count is used as a divisor with no zero guard.
- **Evidence:** `return total / count`

#### `mf-three-defects-09-d3`

| Field | Value |
| --- | --- |
| issue_type | `boundary_condition` |
| severity | `high` |
| file | `app/import_c.py` |
| line_range | 2-2 |
| evidence line in file | 2 |
| deterministic check | supported: range(len(...) + 1) with i used as an index reads one past the end |

- **Description:** range(len(items) + 1) indexes one past the end.
- **Trigger condition:** last() is called with any list; the final iteration raises IndexError.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** range(len(items) + 1) indexes one past the end.
- **Evidence:** `for i in range(len(items) + 1):`

### Author notes

Three independent defects; a single-defect metric would understate recall.

---

## mf-same-line-10

**PR title:** feat: persist upload metadata

**Split:** `test` | **Label status:** `draft` | **Labelled defects:** 2 | **Files:** 1

### `app/persist.py` (modified)

**Content (no base revision in this case):**

```python
import os


def persist(path):
    os.chmod(path, 0o777); TOKEN = "sk-live-77aa88bb99cc"
```
### Proposed ground truth

#### `mf-same-line-10-d1`

| Field | Value |
| --- | --- |
| issue_type | `overly_permissive_file_mode` |
| severity | `high` |
| file | `app/persist.py` |
| line_range | 5-5 |
| evidence line in file | 5 |
| deterministic check | supported: mode 0o777 grants write access to other users |

- **Description:** The file is made world-writable.
- **Trigger condition:** persist() is called on any path.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** The file is made world-writable.
- **Evidence:** `os.chmod(path, 0o777)`

#### `mf-same-line-10-d2`

| Field | Value |
| --- | --- |
| issue_type | `hardcoded_secret` |
| severity | `critical` |
| file | `app/persist.py` |
| line_range | 5-5 |
| evidence line in file | 5 |
| deterministic check | supported: TOKEN is assigned a credential-like literal |

- **Description:** A credential literal is assigned on the same line.
- **Trigger condition:** Reading app/persist.py exposes the token.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** A credential literal is assigned on the same line.
- **Evidence:** `TOKEN = "sk-live-77aa88bb99cc"`

### Author notes

Two distinct defects on one physical line; dedup must not merge them.

---

## mf-deleted-11

**PR title:** chore: drop the legacy exporter

**Split:** `test` | **Label status:** `draft` | **Labelled defects:** 0 | **Files:** 2

**No defect is labelled.** Confirm the change is genuinely clean.

### `app/exporter.py` (removed)

**Content (no base revision in this case):**

```python
def export(path):
    return open(path).read()
```
### `app/new_export.py` (modified)

**Content (no base revision in this case):**

```python
def export(path):
    with open(path) as handle:
        return handle.read()
```
### Author notes

Removed file plus a correct replacement; a finding on the removed path is wrong.

---

## sf-null-12

**PR title:** feat: add display_name

**Split:** `holdout` | **Label status:** `draft` | **Labelled defects:** 1 | **Files:** 1

### `app/profile.py` (modified)

**Content (no base revision in this case):**

```python
from typing import Optional


def display_name(user: Optional[object]) -> str:
    return user.name
```
### Proposed ground truth

#### `sf-null-12-d1`

| Field | Value |
| --- | --- |
| issue_type | `null_dereference` |
| severity | `high` |
| file | `app/profile.py` |
| line_range | 5-5 |
| evidence line in file | 5 |
| deterministic check | supported: 'user' is Optional and read as .name with no dominating None check |

- **Description:** The Optional user is dereferenced without a None check.
- **Trigger condition:** display_name(None) is called, which the annotation permits.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** The Optional user is dereferenced without a None check.
- **Evidence:** `return user.name`

---

## sf-leak-13

**PR title:** feat: add export helper

**Split:** `dev` | **Label status:** `draft` | **Labelled defects:** 1 | **Files:** 1

### `app/export.py` (modified)

**Content (no base revision in this case):**

```python
def export(path):
    handle = open(path)
    return handle.read()
```
### Proposed ground truth

#### `sf-leak-13-d1`

| Field | Value |
| --- | --- |
| issue_type | `resource_leak` |
| severity | `medium` |
| file | `app/export.py` |
| line_range | 2-2 |
| evidence line in file | 2 |
| deterministic check | supported: the reported open() call is outside a with-block and never closed |

- **Description:** The file handle is never closed.
- **Trigger condition:** export() returns or raises before closing.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** The file handle is never closed.
- **Evidence:** `handle = open(path)`

---

## sf-testgap-14

**PR title:** feat: transfer funds between accounts

**Split:** `test` | **Label status:** `draft` | **Labelled defects:** 1 | **Files:** 1

### `app/transfers.py` (modified)

**Content (no base revision in this case):**

```python
def transfer_funds(source, target, amount):
    if amount <= 0:
        raise ValueError('amount must be positive')
    source.debit(amount)
    target.credit(amount)
```
### Proposed ground truth

#### `sf-testgap-14-d1`

| Field | Value |
| --- | --- |
| issue_type | `missing_test_coverage` |
| severity | `medium` |
| file | `app/transfers.py` |
| line_range | 4-5 |
| evidence line in file | 5 |
| deterministic check | UNKNOWN: the changed code is risky but no test content was retrieved, and a bounded context cannot prove absence |

- **Description:** The debit/credit pair has no test for a partial failure.
- **Trigger condition:** credit() fails after debit() succeeded.
- **Expected:** A test covers the credit-failure path and any rollback.
- **Actual:** The debit/credit pair has no test for a partial failure.
- **Evidence:** `target.credit(amount)`

### Author notes

A diff cannot prove the absence of a test; expect UNKNOWN unless a test file is supplied.

---

## safe-param-sql-15

**PR title:** fix: parameterize the user lookup

**Split:** `dev` | **Label status:** `draft` | **Labelled defects:** 0 | **Files:** 1

**No defect is labelled.** Confirm the change is genuinely clean.

### `app/search.py` (modified)

**Content (no base revision in this case):**

```python
def search(cursor, name):
    cursor.execute('SELECT * FROM users WHERE name = %s', (name,))
```
### Author notes

Parameterized query; any sql_injection finding is a false positive.

---

## safe-subprocess-16

**PR title:** fix: run the tool without a shell

**Split:** `dev` | **Label status:** `indeterminate` | **Labelled defects:** 0 | **Files:** 1

**No defect is labelled.** Confirm the change is genuinely clean.

### `app/runner.py` (modified)

**Content (no base revision in this case):**

```python
import subprocess


def run(program, args):
    return subprocess.run([program, *args], check=True, capture_output=True)
```
### Author notes

Argument vector, no shell. A missing_exception_handling claim on line 5 is arguable; see the arbitration report.

---

## safe-with-open-17

**PR title:** refactor: use a context manager

**Split:** `dev` | **Label status:** `draft` | **Labelled defects:** 0 | **Files:** 1

**No defect is labelled.** Confirm the change is genuinely clean.

### `app/read.py` (modified)

**Content (no base revision in this case):**

```python
def read(path):
    with open(path) as handle:
        return handle.read()
```
---

## safe-guard-18

**PR title:** fix: guard the Optional user

**Split:** `dev` | **Label status:** `draft` | **Labelled defects:** 0 | **Files:** 1

**No defect is labelled.** Confirm the change is genuinely clean.

### `app/profile.py` (modified)

**Content (no base revision in this case):**

```python
from typing import Optional


def display_name(user: Optional[object]) -> str:
    if user is None:
        return ''
    return user.name
```
---

## safe-env-secret-19

**PR title:** fix: read the token from the environment

**Split:** `dev` | **Label status:** `draft` | **Labelled defects:** 0 | **Files:** 1

**No defect is labelled.** Confirm the change is genuinely clean.

### `app/config.py` (modified)

**Content (no base revision in this case):**

```python
import os

API_TOKEN = os.getenv('API_TOKEN')
```
### Author notes

Secret-named variable, but it is loaded from the environment.

---

## tricky-constant-sql-20

**PR title:** feat: count all users

**Split:** `test` | **Label status:** `draft` | **Labelled defects:** 0 | **Files:** 1

**No defect is labelled.** Confirm the change is genuinely clean.

### `app/count.py` (modified)

**Content (no base revision in this case):**

```python
def count(cursor):
    cursor.execute('SELECT COUNT(*) FROM users')
    return cursor.fetchone()
```
### Author notes

A query call with no interpolation point; an injection claim must be refuted.

---

## tricky-mixed-sql-21

**PR title:** feat: add safe and unsafe lookups

**Split:** `test` | **Label status:** `draft` | **Labelled defects:** 1 | **Files:** 1

### `app/search.py` (modified)

**Content (no base revision in this case):**

```python
def search(cursor, name, mode):
    if mode == 'safe':
        cursor.execute('SELECT * FROM users WHERE name = %s', (name,))
    else:
        cursor.execute(f'SELECT * FROM users WHERE name = {name}')
```
### Proposed ground truth

#### `tricky-mixed-sql-21-d1`

| Field | Value |
| --- | --- |
| issue_type | `sql_injection` |
| severity | `critical` |
| file | `app/search.py` |
| line_range | 5-5 |
| evidence line in file | 5 |
| deterministic check | supported: query string is built by interpolation |

- **Description:** The else branch interpolates name into the query.
- **Trigger condition:** mode is anything other than 'safe'.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** The else branch interpolates name into the query.
- **Evidence:** `cursor.execute(f'SELECT * FROM users WHERE name = {name}')`

### Author notes

One function holds a safe and an unsafe call. A finding on line 3 is wrong; on line 5 is right.

---

## tricky-guard-not-dominating-22

**PR title:** feat: print the last item

**Split:** `test` | **Label status:** `draft` | **Labelled defects:** 1 | **Files:** 1

### `app/batch.py` (modified)

**Content (no base revision in this case):**

```python
def last(items):
    for i in range(len(items) + 1):
        print(items[i])
        if i >= len(items):
            break
```
### Proposed ground truth

#### `tricky-guard-not-dominating-22-d1`

| Field | Value |
| --- | --- |
| issue_type | `boundary_condition` |
| severity | `high` |
| file | `app/batch.py` |
| line_range | 2-2 |
| evidence line in file | 2 |
| deterministic check | supported: range(len(...) + 1) with i used as an index reads one past the end |

- **Description:** The bounds check runs after the read, so it cannot prevent the IndexError.
- **Trigger condition:** last() is called with any list; the final iteration reads items[len(items)] first.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** The bounds check runs after the read, so it cannot prevent the IndexError.
- **Evidence:** `for i in range(len(items) + 1):`

### Author notes

The guard exists but does not dominate the read.

---

## tricky-guard-dominating-23

**PR title:** feat: print the last item safely

**Split:** `test` | **Label status:** `draft` | **Labelled defects:** 0 | **Files:** 1

**No defect is labelled.** Confirm the change is genuinely clean.

### `app/batch.py` (modified)

**Content (no base revision in this case):**

```python
def last(items):
    for i in range(len(items) + 1):
        if i >= len(items):
            break
        print(items[i])
```
### Author notes

Mirror of 22 with the guard before the read; a boundary claim must be refuted.

---

## tricky-evidence-no-defect-24

**PR title:** feat: document the injection risk

**Split:** `test` | **Label status:** `draft` | **Labelled defects:** 0 | **Files:** 1

**No defect is labelled.** Confirm the change is genuinely clean.

### `app/notes.py` (modified)

**Content (no base revision in this case):**

```python
SQL_INJECTION_NOTE = 'do not interpolate user input into a query'
```
### Author notes

The words appear in a constant; text matching must not become proof.

---

## tricky-rename-both-sides-25

**PR title:** refactor: rename User.name to User.full_name

**Split:** `test` | **Label status:** `draft` | **Labelled defects:** 0 | **Files:** 2

**No defect is labelled.** Confirm the change is genuinely clean.

### `app/models.py` (modified)

**Content (no base revision in this case):**

```python
class User:
    def __init__(self, full_name: str) -> None:
        self.full_name = full_name
```
### `app/views.py` (modified)

**Content (no base revision in this case):**

```python
def label(user):
    return user.full_name
```
### Author notes

Both sides renamed; a cross_file_breakage claim here is a false positive.

---

## tricky-two-agents-one-defect-26

**PR title:** feat: add chmod helper

**Split:** `holdout` | **Label status:** `draft` | **Labelled defects:** 1 | **Files:** 1

### `app/uploads.py` (modified)

**Content (no base revision in this case):**

```python
import os


def save(path):
    os.chmod(path, 0o777)
```
### Proposed ground truth

#### `tricky-two-agents-one-defect-26-d1`

| Field | Value |
| --- | --- |
| issue_type | `overly_permissive_file_mode` |
| severity | `high` |
| file | `app/uploads.py` |
| line_range | 5-5 |
| evidence line in file | 5 |
| deterministic check | supported: mode 0o777 grants write access to other users |

- **Description:** save() makes the file writable by everyone.
- **Trigger condition:** save() is called on any path.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** save() makes the file writable by everyone.
- **Evidence:** `os.chmod(path, 0o777)`

### Author notes

Two agents will report this with different wording; it must count once.

---

## tricky-adjacent-same-type-27

**PR title:** feat: write two files

**Split:** `holdout` | **Label status:** `draft` | **Labelled defects:** 2 | **Files:** 1

### `app/writer.py` (modified)

**Content (no base revision in this case):**

```python
import os


def write_a(path):
    os.chmod(path, 0o777)


def write_b(path):
    os.chmod(path, 0o777)
```
### Proposed ground truth

#### `tricky-adjacent-same-type-27-d1`

| Field | Value |
| --- | --- |
| issue_type | `overly_permissive_file_mode` |
| severity | `high` |
| file | `app/writer.py` |
| line_range | 5-5 |
| evidence line in file | not uniquely located |
| deterministic check | supported: mode 0o777 grants write access to other users |

- **Description:** write_a makes the file world-writable.
- **Trigger condition:** write_a() is called on any path.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** write_a makes the file world-writable.
- **Evidence:** `os.chmod(path, 0o777)`

#### `tricky-adjacent-same-type-27-d2`

| Field | Value |
| --- | --- |
| issue_type | `overly_permissive_file_mode` |
| severity | `high` |
| file | `app/writer.py` |
| line_range | 9-9 |
| evidence line in file | not uniquely located |
| deterministic check | supported: mode 0o777 grants write access to other users |

- **Description:** write_b makes the file world-writable.
- **Trigger condition:** write_b() is called on any path.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** write_b makes the file world-writable.
- **Evidence:** `os.chmod(path, 0o777)`

### Author notes

Identical evidence, same type, four lines apart: two real defects that must NOT be merged.

---

## tricky-multiline-sql-28

**PR title:** feat: build a filtered query

**Split:** `test` | **Label status:** `indeterminate` | **Labelled defects:** 1 | **Files:** 1

### `app/report.py` (modified)

**Content (no base revision in this case):**

```python
def report(cursor, table):
    query = 'SELECT * FROM ' + table
    cursor.execute(query)
```
### Proposed ground truth

#### `tricky-multiline-sql-28-d1`

| Field | Value |
| --- | --- |
| issue_type | `sql_injection` |
| severity | `critical` |
| file | `app/report.py` |
| line_range | 2-2 |
| evidence line in file | 2 |
| deterministic check | UNKNOWN: the query argument is not statically classifiable |

- **Description:** table is concatenated into the query string.
- **Trigger condition:** table comes from user input.
- **Expected:** Validate the table against an allow-list; identifiers cannot be bound.
- **Actual:** table is concatenated into the query string.
- **Evidence:** `query = 'SELECT * FROM ' + table`

### Author notes

Interprocedural: the sink is on line 3 but the concatenation is on line 2. Layer 2 may answer UNKNOWN.

---

## tricky-late-import-29

**PR title:** feat: parse queued payloads

**Split:** `dev` | **Label status:** `draft` | **Labelled defects:** 1 | **Files:** 1

### `app/queue.py` (modified)

**Content (no base revision in this case):**

```python
def parse(text):
    import json
    return json.loads(text)
```
### Proposed ground truth

#### `tricky-late-import-29-d1`

| Field | Value |
| --- | --- |
| issue_type | `missing_exception_handling` |
| severity | `medium` |
| file | `app/queue.py` |
| line_range | 3-3 |
| evidence line in file | 3 |
| deterministic check | supported: json.loads escapes without a handler at this location |

- **Description:** json.loads raises ValueError on malformed input and nothing handles it.
- **Trigger condition:** parse() receives a non-JSON payload.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** json.loads raises ValueError on malformed input and nothing handles it.
- **Evidence:** `return json.loads(text)`

---

## tricky-docstring-only-30

**PR title:** docs: expand the module docstring

**Split:** `dev` | **Label status:** `draft` | **Labelled defects:** 0 | **Files:** 1

**No defect is labelled.** Confirm the change is genuinely clean.

### `app/module.py` (modified)

**Content (no base revision in this case):**

```python
"""Helpers for the review pipeline.

No behaviour change."""

VERSION = "1.2"
```
---

## tricky-deleted-target-31

**PR title:** chore: remove an unused helper

**Split:** `holdout` | **Label status:** `draft` | **Labelled defects:** 0 | **Files:** 2

**No defect is labelled.** Confirm the change is genuinely clean.

### `app/old.py` (removed)

**Content (no base revision in this case):**

```python
def old_helper():
    return 1
```
### `app/current.py` (modified)

**Content (no base revision in this case):**

```python
def current_helper():
    return 2
```
### Author notes

A finding citing app/old.py must not be recovered onto app/current.py.

---

## tricky-windows-path-32

**PR title:** feat: add an export path helper

**Split:** `holdout` | **Label status:** `draft` | **Labelled defects:** 1 | **Files:** 2

### `app/paths.py` (modified)

**Content (no base revision in this case):**

```python
def export_path(root):
    return root + '/out.csv'
```
### `app/export.py` (modified)

**Content (no base revision in this case):**

```python
def export(root):
    handle = open(root + '/out.csv')
    return handle.read()
```
### Proposed ground truth

#### `tricky-windows-path-32-d1`

| Field | Value |
| --- | --- |
| issue_type | `resource_leak` |
| severity | `medium` |
| file | `app/export.py` |
| line_range | 2-2 |
| evidence line in file | 2 |
| deterministic check | supported: the reported open() call is outside a with-block and never closed |

- **Description:** The handle opened in export is never closed.
- **Trigger condition:** export() returns before closing.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** The handle opened in export is never closed.
- **Evidence:** `handle = open(root + '/out.csv')`

### Author notes

Two files with similar names; a Windows-style prediction (app\\export.py) must normalize here.

---

## real-diff-context-33

**PR title:** fix: change one line of the retry helper

**Split:** `dev` | **Label status:** `draft` | **Labelled defects:** 1 | **Files:** 1

### `app/retry.py` (modified)

**Before (base revision):**

```python
def retry(times):
    if times <= 0:
        raise ValueError('times must be positive')
    for attempt in range(times):
        yield attempt
```
**After (head revision):**

```python
def retry(times):
    if times <= 0:
        raise ValueError('times must be positive')
    for attempt in range(times + 1):
        yield attempt
```
### Proposed ground truth

#### `real-diff-context-33-d1`

| Field | Value |
| --- | --- |
| issue_type | `boundary_condition` |
| severity | `high` |
| file | `app/retry.py` |
| line_range | 4-4 |
| evidence line in file | 4 |
| deterministic check | UNKNOWN: the loop bound relation is not statically decidable |

- **Description:** range(times + 1) yields one more attempt than requested.
- **Trigger condition:** retry(3) yields four attempts.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** range(times + 1) yields one more attempt than requested.
- **Evidence:** `    for attempt in range(times + 1):`

### Author notes

Real diff with context: lines 1-3 are unchanged context. A finding there must be marked as reported on unchanged code, not as a change.

---

## real-diff-rename-34

**PR title:** refactor: rename helpers.py to utilities.py

**Split:** `holdout` | **Label status:** `draft` | **Labelled defects:** 1 | **Files:** 1

### `app/utilities.py` (renamed)

**Before (base revision):**

```python
def helper(path):
    handle = open(path)
    return handle.read()
```
**After (head revision):**

```python
def helper(path):
    handle = open(path)
    return handle.read()
```
### Proposed ground truth

#### `real-diff-rename-34-d1`

| Field | Value |
| --- | --- |
| issue_type | `resource_leak` |
| severity | `medium` |
| file | `app/utilities.py` |
| line_range | 2-2 |
| evidence line in file | 2 |
| deterministic check | supported: the reported open() call is outside a with-block and never closed |

- **Description:** The handle opened in helper is never closed.
- **Trigger condition:** helper() returns before closing the handle.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** The handle opened in helper is never closed.
- **Evidence:** `    handle = open(path)`

### Author notes

Pure rename with an unchanged body. A finding may legitimately cite the old path app/helpers.py; PathResolver must map it forward, and the diff carries rename metadata.

---

## real-diff-deletion-35

**PR title:** fix: remove the unsafe chmod call

**Split:** `test` | **Label status:** `draft` | **Labelled defects:** 0 | **Files:** 1

**No defect is labelled.** Confirm the change is genuinely clean.

### `app/uploads.py` (modified)

**Before (base revision):**

```python
import os


def save(path):
    os.chmod(path, 0o777)
    return path
```
**After (head revision):**

```python
import os


def save(path):
    return path
```
### Author notes

The defect was removed. The diff contains a deleted line; a finding still citing os.chmod must be rejected because that line is gone from the head revision.

---

## real-diff-guard-line-36

**PR title:** feat: filter the empty rows

**Split:** `test` | **Label status:** `indeterminate` | **Labelled defects:** 1 | **Files:** 1

### `app/rows.py` (modified)

**Before (base revision):**

```python
def non_empty(rows):
    result = []
    for row in rows:
        if row:
            result.append(row)
    return result
```
**After (head revision):**

```python
def non_empty(rows):
    result = []
    for row in rows:
        if row:
            result.append(row[0])
    return result
```
### Proposed ground truth

#### `real-diff-guard-line-36-d1`

| Field | Value |
| --- | --- |
| issue_type | `boundary_condition` |
| severity | `medium` |
| file | `app/rows.py` |
| line_range | 5-5 |
| evidence line in file | 5 |
| deterministic check | UNKNOWN: indexing is present but no loop bound can be inspected |

- **Description:** row[0] is read without checking that the row has elements.
- **Trigger condition:** row is a non-empty list or string whose first element is intended.
- **Expected:** The unsafe value is validated or contained before use.
- **Actual:** row[0] is read without checking that the row has elements.
- **Evidence:** `            result.append(row[0])`

### Author notes

The guard `if row:` already excludes empty rows, so row[0] is arguably safe. Kept as a disputed label: the reviewer decides, and the case measures whether a one-line-off finding still matches.

---

