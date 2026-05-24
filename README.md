# tes-client

A Python client for the [GA4GH Task Execution Service (TES)](https://ga4gh.github.io/task-execution-schemas/) API.

TES is a standard REST API for submitting and monitoring computational tasks on distributed compute infrastructure. This library lets you build a TES task message using a fluent Python API, submit it to a TES endpoint, and track it through to completion — with built-in support for Keycloak OIDC authentication.


---

## Installation

```
pip install tes-client
```

Or install directly from source:

```
pip install git+https://github.com/you/tes-client.git
```

Requires Python 3.11+.

---

## What is a TES task?

A TES task describes a unit of computation as:

| Section | Purpose |
|---|---|
| **Inputs** | Files to stage in before execution (pulled from a URL into a container path) |
| **Outputs** | Files to stage out after execution (pushed from a container path to a URL) |
| **Resources** | CPU cores, RAM, disk, and scheduling hints |
| **Executors** | One or more Docker containers to run in sequence |
| **Tags** | Arbitrary key/value metadata (e.g. project, queue) |

The task is submitted as a JSON document to the TES REST API. This library constructs that document, handles authentication, and polls for completion.

---

## Quick start

### 1. Choose an authentication method

**No authentication** (open or local dev endpoint):

```python
from tes_client import NoAuth

auth = NoAuth()
```

**Client credentials** (machine-to-machine, Keycloak service account):

```python
from tes_client import ClientCredentialsAuth

auth = ClientCredentialsAuth(
    base_url="https://keycloak.example.org",
    realm="my-realm",
    client_id="tes-service-account",
    client_secret="super-secret",
)
```

**Username and password** (interactive user, ROPC grant):

```python
from tes_client import PasswordAuth

auth = PasswordAuth(
    base_url="https://keycloak.example.org",
    realm="my-realm",
    client_id="tes-client",
    username="alice",
    password="hunter2",
)
```

**Authorization code + PKCE** (browser pop-up, most secure for interactive use):

```python
from tes_client import AuthorizationCodeAuth

auth = AuthorizationCodeAuth(
    base_url="https://keycloak.example.org",
    realm="my-realm",
    client_id="tes-public-client",  # public client — no secret needed
    redirect_port=8080,             # must match the redirect URI registered in Keycloak
)
```

On first use this opens your default browser at the Keycloak login page and waits for you to authenticate. After that the refresh token is used silently until it expires, at which point the browser opens again.

All four auth classes are drop-in replacements — the rest of the code is identical regardless of which you choose.

#### Checking and refreshing auth in scripts

Every auth class exposes two helpers for long-running scripts where a token may expire mid-run:

| Method | Description |
|---|---|
| `auth.is_valid()` | Returns `True` if the cached token is still usable — no network call. |
| `auth.ensure_valid()` | Re-authenticates now if the token is expired or missing; no-op if still valid. |

`ensure_valid()` uses the same logic as `auth_header()` — it reuses a refresh token where available, and only falls back to a full grant (or browser pop-up for `AuthorizationCodeAuth`) when necessary.

```python
# Before a batch of submissions in a long-running script
if not auth.is_valid():
    print("Token expired — re-authenticating…")
auth.ensure_valid()

task_id = client.submit(task)
```

Or in a polling loop:

```python
for item in work_queue:
    auth.ensure_valid()   # silent no-op if token is still good
    client.submit(build_task(item))
```

---

### 2. Create a client

```python
from tes_client import TesClient

client = TesClient(
    tes_url="https://tes.example.org",
    token_manager=auth,
)
```

---

### 3. Build a task

Tasks are built with a fluent (method-chaining) API. Each method returns the task itself so calls can be chained together.

```python
from tes_client import TesTask

task = (
    TesTask(name="my-analysis")
    .set_project_tag("DPUK_Project55")   # tag: project
    .set_tres_tag("DPUK")                # tag: tres (compute queue)
    .add_input(
        path="/inputs/data.bam",         # path inside the container
        url="s3://my-bucket/data.bam",   # where to pull it from
    )
    .add_output(
        path="/outputs/result.vcf",      # path inside the container
        url="s3://my-bucket/results/result.vcf",  # where to push it after
    )
    .set_resources(cpu_cores=4, ram_gb=8, disk_gb=50)
    .add_executor(
        image="biocontainers/samtools:1.18",
        command=["samtools", "view", "-o", "/outputs/result.vcf", "/inputs/data.bam"],
    )
)
```

You can inspect the JSON that will be sent before submitting:

```python
print(task.submission_json())
```

```json
{
  "name": "my-analysis",
  "inputs": [
    {
      "url": "s3://my-bucket/data.bam",
      "path": "/inputs/data.bam",
      "type": "FILE"
    }
  ],
  "outputs": [
    {
      "url": "s3://my-bucket/results/result.vcf",
      "path": "/outputs/result.vcf",
      "type": "FILE"
    }
  ],
  "resources": {
    "cpu_cores": 4,
    "ram_gb": 8.0,
    "disk_gb": 50.0
  },
  "executors": [
    {
      "image": "biocontainers/samtools:1.18",
      "command": ["samtools", "view", "-o", "/outputs/result.vcf", "/inputs/data.bam"]
    }
  ],
  "tags": {
    "project": "DPUK_Project55",
    "tres": "DPUK"
  }
}
```

---

### 4. Submit and track

```python
task_id = client.submit(task)
print(f"Submitted: {task_id}")
```

`TaskTracker` polls the TES API and prints each state transition. `wait()` blocks until the task reaches a terminal state (`COMPLETE`, `EXECUTOR_ERROR`, `SYSTEM_ERROR`, or `CANCELED`).

```python
from tes_client import TaskTracker

tracker = TaskTracker(client, task_id, poll_interval=15.0)
final = tracker.wait(timeout=3600)  # raises TimeoutError after 1 hour

if final.state.is_failure():
    print(f"Task failed: {final.state}")
    tracker.report()  # prints executor logs and exit codes
else:
    print("Task completed successfully.")
```

Example output while running:

```
[TES a1b2c3d4] QUEUED
[TES a1b2c3d4] INITIALIZING
[TES a1b2c3d4] RUNNING
[TES a1b2c3d4] COMPLETE
```

You can also pass a callback that fires on every state change:

```python
def on_change(task):
    print(f"State changed to: {task.state}")

tracker = TaskTracker(client, task_id, on_state_change=on_change)
```

---

## Other client operations

```python
# Fetch a task (MINIMAL view by default, FULL includes executor logs)
task = client.get(task_id, full=True)

# List tasks
tasks = client.list_tasks(name_prefix="my-", page_size=20)

# Cancel a running task
client.cancel(task_id)

# Check the TES service info
info = client.service_info()

# Just get the current state
state = client.state(task_id)
```

---

## TES task states

| State | Meaning |
|---|---|
| `QUEUED` | Accepted, waiting for resources |
| `INITIALIZING` | Pulling images and staging inputs |
| `RUNNING` | Executors are running |
| `COMPLETE` | All executors finished with exit code 0 |
| `EXECUTOR_ERROR` | An executor exited with a non-zero code |
| `SYSTEM_ERROR` | Infrastructure-level failure |
| `CANCELED` | Canceled by request |

`state.is_terminal()` returns `True` for the last four. `state.is_failure()` returns `True` for `EXECUTOR_ERROR`, `SYSTEM_ERROR`, and `CANCELED`.

---

## Requirements

- Python 3.11+
- [httpx](https://www.python-httpx.org/) >= 0.27
- [pydantic](https://docs.pydantic.dev/) >= 2.0
