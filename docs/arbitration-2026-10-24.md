# Arbitration: two disputed findings from the first online run

Two false positives survived the first real-LLM run. Both are `missing_exception_handling`
and both sit on code that is otherwise safe. This document examines them without changing
any historical label or result: `prguard_v1.json` and every recorded score stay exactly as
they were.

Both cases are marked **indeterminate as labels**, for different reasons.

---

## Case 1: `shell-injection-06`

### Code

```python
import subprocess

def run(user_arg):
    subprocess.run(user_arg, shell=True)
```

Labelled defect: `unsafe_command` at line 4.
The agent additionally reported: `missing_exception_handling` at line 4 -
"subprocess.run 未检查返回码，失败被静默吞掉".

### 1. What is concretely wrong in the code?

Two separate facts, and they are not equally serious:

1. `shell=True` with a string argument means the command line is parsed by a shell.
   `run("ls; rm -rf ~")` executes both commands. This is the labelled defect and it is
   unambiguous.
2. `subprocess.run` is called without `check=True` and its return value is discarded, so
   `returncode` is never inspected. A non-zero exit is silently ignored.

### 2. When does it actually trigger?

* Fact 1: whenever `user_arg` contains shell metacharacters (`;`, `|`, `&&`, `$()`), or
  whenever it is attacker-influenced at all.
* Fact 2: whenever the child process exits non-zero. The function then returns `None`
  and the caller has no way to know the command failed.

### 3. Bug or normal design?

* Fact 1: **a real bug**, critical severity.
* Fact 2: **arguable**. For a fire-and-forget helper, ignoring the exit code may be
  intentional. For anything whose result matters it is a defect. The fixture does not
  label it, and nothing in the code says which contract `run` intends.

### 4. Is the agent's suggestion reasonable?

The agent suggested handling the failure. That is a defensible suggestion for fact 2, and
it is an *advisory-grade* recommendation rather than a provable defect: the code does not
contradict "the caller does not care about failure". The verifier's current behaviour,
returning `SUPPORTED` for `missing_exception_handling` because `subprocess.run` is a
raising call not inside a `try`, is technically right about the *absence of a handler* and
silent about whether one is *needed*.

### 5. Is the evidence sufficient?

* For fact 1: yes, fully.
* For fact 2: sufficient to establish "the exit code is unchecked"; **not** sufficient to
  establish "this is wrong". -> **indeterminate**.

### Verdict

| Claim | Label status | Reason |
| --- | --- | --- |
| `unsafe_command` (labelled) | correct, keep | provable from `shell=True` |
| `missing_exception_handling` (reported) | **indeterminate** | the observation is true, defect status depends on an unwritten contract |

The historical result stands: this finding is a false positive under the fixture's labels.
It is a false positive caused by **label incompleteness**, not by verifier error.

---

## Case 2: `safe-subprocess-13`

### Code

```python
import subprocess

def run(program, args):
    return subprocess.run([program, *args], check=True, capture_output=True)
```

Labelled: no defect (`safe_argument_vector`).
The agent reported: `missing_exception_handling` at line 4 -
"run() 在子进程退出码非零时抛出未处理的 CalledProcessError".

### 1. What is concretely wrong in the code?

Nothing about shell injection: an argument vector plus no `shell=True` means no shell is
involved. This is the correct way to call a subprocess.

The agent's observation is factually accurate and about a different concern: `check=True`
raises `CalledProcessError` when the child exits non-zero, and this function does not
catch it.

### 2. When does it actually trigger?

Whenever the child process exits non-zero. The exception propagates to the caller.

### 3. Bug or normal design?

**Normal design.** Letting the exception propagate is the idiomatic contract for a thin
wrapper: the caller decides whether a failed subprocess is recoverable. Converting it to
a silent `None` would usually be worse. There is no state to corrupt, no resource to leak,
and no security impact.

So this is **not a bug**, and the fixture's "no defect" label is correct.

### 4. Is the agent's suggestion reasonable?

The suggestion ("wrap the call and handle the failure") is a style preference here. It
would be reasonable in an application entry point; it is not a defect in a library
helper. As a reviewer comment it is low-value but not absurd.

### 5. Is the evidence sufficient?

Enough to say "an exception is not caught". Not enough to say "it should be". The
distinction needs the caller's contract, which a diff cannot show. -> **indeterminate**
as a defect, and **not a defect** under the fixture's stated intent.

### Verdict

| Claim | Label status | Reason |
| --- | --- | --- |
| safe argument vector (labelled) | correct, keep | no shell involved |
| `missing_exception_handling` (reported) | **indeterminate, leaning not-a-defect** | propagation is by design for a wrapper |

---

## What this says about the verifier

Layer 2 answers "is there really no handler?" and it answers correctly in both cases.
It does not and cannot answer "does this code need one?", because that is a design
contract rather than a syntactic fact.

Two options exist, and neither was taken in this round because both would change scores
on a frozen dataset:

1. Restrict `missing_exception_handling` to sinks where an unhandled exception provably
   corrupts state or leaks a resource, and return `UNKNOWN` for plain propagation.
   This would remove both false positives but also lose recall on `exception-02`, which
   is a genuine defect of the same shape.
2. Add a `needs_human_review` band for "true observation, unknown severity", and report
   it outside the defect count.

The honest conclusion is that **`missing_exception_handling` is the weakest category in
the taxonomy**: it is syntactically decidable and semantically ambiguous. It is recorded
here as a known limitation rather than patched into the metric.

## Why the ground truth was not edited

Changing `shell-injection-06` or `safe-subprocess-13` to add the second defect would
raise measured precision from 0.800 to 1.000 on the frozen run. That number would then be
the product of a label edit rather than a pipeline improvement, and every earlier result
would silently become incomparable. The labels stay as they are.
