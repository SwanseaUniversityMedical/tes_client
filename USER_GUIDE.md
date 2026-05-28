# User Guide: GA4GH TES and the `tes-client` Python Library

## Contents

1. [What is GA4GH TES?](#1-what-is-ga4gh-tes)
2. [A quick note on containers (Docker)](#2-a-quick-note-on-containers-docker)
3. [How TES works](#3-how-tes-works)
4. [Anatomy of a TES task message](#4-anatomy-of-a-tes-task-message)
5. [Using this library](#5-using-this-library)
   - [Installation](#51-installation)
   - [Authentication](#52-authentication)
   - [Building a task](#53-building-a-task)
   - [Connecting to a TES server](#54-connecting-to-a-tes-server)
   - [Submitting and tracking a task](#55-submitting-and-tracking-a-task)
   - [Other useful operations](#56-other-useful-operations)
   - [Error handling](#57-error-handling)
6. [Complete worked examples](#6-complete-worked-examples)
7. [Quick reference](#7-quick-reference)

---

## 1. What is GA4GH TES?

**TES** stands for **Task Execution Service**. It is a standard REST API specification published by the [Global Alliance for Genomics and Health (GA4GH)](https://www.ga4gh.org/) — an international consortium that creates open standards for sharing and working with genomic and health data.

### The problem it solves

Imagine you have an analysis you want to run — perhaps processing some genomic sequences, training a model on a dataset, or running a statistical pipeline. You want to send that job to a compute cluster or cloud platform. But every compute system has its own submission format: HPC schedulers use job scripts in their own syntax, AWS uses its own API, Google Cloud uses another, and your local Kubernetes cluster uses yet another.

TES solves this by providing a **single, standard way to describe and submit computational tasks**, regardless of what runs underneath. You write your task once, and any TES-compatible platform can run it — whether that's an HPC cluster at a university, a cloud VM, or a containerised environment on a secure data enclave.

### What TES actually does

When you submit a task to a TES server, you are telling it:

1. **What environment to use** — a Docker container image (e.g. a specific version of `samtools` or `python`)
2. **What command to run** — the exact command and arguments to execute inside that container
3. **What files to bring in** — input data files, fetched from a URL before the container starts
4. **What files to take out** — output files, pushed to a destination URL after the container finishes
5. **What resources you need** — CPU cores, RAM, and disk space

The TES server handles everything else: scheduling the job, pulling the Docker image, moving files in and out, and reporting progress back to you.

### Who is TES for?

TES is particularly common in the biomedical and genomics research community, but it is a general standard suitable for any scientific computation. It is widely used in platforms that need to run analysis tasks on sensitive data in controlled environments — such as trusted research environments (TREs) and secure data enclaves — because it allows users to submit computation without ever directly touching the underlying infrastructure.

---

## 2. A quick note on containers (Docker)

If you are already comfortable with Docker and containerisation, you can skip this section.

A **container** is a lightweight, self-contained package that includes everything a piece of software needs to run: the application code, its dependencies, system libraries, and configuration. Think of it like a standardised shipping container — it can be loaded onto any ship (any compute platform) and the contents remain consistent.

**Docker** is the most widely used container technology. When we say a TES task uses the `ubuntu` image or `biocontainers/samtools:1.18`, we mean it will run inside a pre-built Docker container that already has that software installed and ready to use. You do not need to install anything on the compute node — the container brings its own environment.

For a TES task, the key things to understand are:

- The **image** is the name of a Docker container (e.g. `python:3.11`, `ubuntu`, `biocontainers/samtools:1.18`). These can be pulled from public registries like Docker Hub.
- The **command** is what runs inside that container — just like a shell command.
- **Paths inside the container** (like `/inputs/data.bam`) are separate from paths on the host system. Your input files are staged into those paths before your command runs.

---

## 3. How TES works

### The lifecycle of a TES task

When you submit a task to a TES server, it moves through a sequence of states:

```
Submitted → QUEUED → INITIALIZING → RUNNING → COMPLETE
                                             ↘ EXECUTOR_ERROR
                                             ↘ SYSTEM_ERROR
                                             ↘ CANCELED
```

| State | What's happening |
|---|---|
| `QUEUED` | The task has been accepted and is waiting for compute resources to become available |
| `INITIALIZING` | The TES server is pulling the Docker image and staging input files into the container |
| `RUNNING` | Your command is executing inside the container |
| `COMPLETE` | All executors finished with exit code 0 — success |
| `EXECUTOR_ERROR` | Your command returned a non-zero exit code (e.g. the tool crashed, a file was missing) |
| `SYSTEM_ERROR` | An infrastructure-level failure occurred (node crashed, storage unavailable, etc.) |
| `CANCELING` | A cancellation has been requested and is being processed |
| `CANCELED` | The task was successfully cancelled |

Once a task reaches `COMPLETE`, `EXECUTOR_ERROR`, `SYSTEM_ERROR`, or `CANCELED`, it is in a **terminal state** — it will not change again.

### How TES handles files

TES uses a **stage in / stage out** model for files:

- **Before** your command runs, TES downloads your input files from the URLs you specified and places them at the paths you defined inside the container.
- **After** your command finishes (successfully), TES uploads the output files from their container paths to the URLs you specified.

This means your analysis tool sees local files (e.g. `/inputs/sample.bam`) and writes output to local paths (e.g. `/outputs/result.flagstat`). TES handles all the data movement transparently. The URLs can point to object storage (S3, GCS, Azure Blob), HTTP endpoints, or other supported storage backends — depending on what your TES server supports.

### The REST API

TES communicates via a standard HTTP REST API. The main endpoints are:

| Operation | HTTP method | Path |
|---|---|---|
| Submit a task | `POST` | `/v1/tasks` |
| Get task status | `GET` | `/v1/tasks/{id}` |
| List tasks | `GET` | `/v1/tasks` |
| Cancel a task | `POST` | `/v1/tasks/{id}:cancel` |
| Server information | `GET` | `/v1/service-info` |

The `tes-client` library wraps all of these for you — you never need to write raw HTTP calls.

---

## 4. Anatomy of a TES task message

A TES task is a JSON document sent to the server. Understanding its structure helps you build tasks correctly and debug problems when they arise. Here is a complete example, followed by an explanation of each part:

```json
{
  "name": "my-samtools-analysis",
  "description": "Count aligned reads in a BAM file",
  "inputs": [
    {
      "url": "s3://my-bucket/data/sample.bam",
      "path": "/inputs/sample.bam",
      "type": "FILE"
    }
  ],
  "outputs": [
    {
      "url": "s3://my-bucket/results/sample.flagstat",
      "path": "/outputs/sample.flagstat",
      "type": "FILE"
    }
  ],
  "resources": {
    "cpu_cores": 2,
    "ram_gb": 4.0,
    "disk_gb": 20.0
  },
  "executors": [
    {
      "image": "biocontainers/samtools:1.18",
      "command": ["samtools", "flagstat", "/inputs/sample.bam"],
      "stdout": "/outputs/sample.flagstat"
    }
  ],
  "tags": {
    "project": "DPUK_Project55",
    "tres": "DPUK"
  }
}
```

### Top-level fields

| Field | Required? | Description |
|---|---|---|
| `name` | No | A human-readable name for the task — useful for finding it in task lists |
| `description` | No | A longer description |
| `inputs` | No | Files to stage into the container before execution |
| `outputs` | No | Files to stage out of the container after execution |
| `resources` | No | Compute resource requirements |
| `executors` | **Yes** | The containers and commands to run — at least one is required |
| `tags` | No | Arbitrary key/value metadata |
| `volumes` | No | Ephemeral scratch directories created inside the container |

### Inputs

Each input describes a file (or directory) to fetch and place inside the container:

```json
{
  "url": "s3://my-bucket/data/sample.bam",   // where to fetch it from
  "path": "/inputs/sample.bam",              // where it appears inside the container
  "type": "FILE"                             // FILE or DIRECTORY
}
```

- `url` — where to download the file from. Can be `s3://`, `gs://`, `http://`, or other schemes supported by your TES server. Can be omitted if using `content` instead.
- `path` — the path *inside the container* where the file will appear. Your command sees this path.
- `type` — `FILE` (default) or `DIRECTORY`.
- `content` — instead of a URL, you can embed small text content directly in the message (useful for config files or short scripts).

### Outputs

Each output describes a file to upload after execution:

```json
{
  "url": "s3://my-bucket/results/sample.flagstat",  // where to push the file to
  "path": "/outputs/sample.flagstat",               // where the file lives inside the container
  "type": "FILE"
}
```

- Both `url` and `path` are required for outputs.
- The file at `path` inside the container is uploaded to `url` after all executors complete.

### Resources

Specify how much compute you need. Any field you omit uses the server's default:

```json
{
  "cpu_cores": 2,         // number of CPU cores
  "ram_gb": 4.0,          // RAM in gigabytes
  "disk_gb": 20.0,        // local disk space in gigabytes
  "preemptible": false,   // allow the job to run on preemptible/spot instances
  "zones": ["us-east1"]   // preferred geographic zones (cloud platforms)
}
```

### Executors

Executors are the core of a TES task — they define what actually runs. You can have more than one executor; they run **in order**, and if any executor fails, the task stops.

```json
{
  "image": "biocontainers/samtools:1.18",           // Docker image to use
  "command": ["samtools", "flagstat", "/inputs/sample.bam"], // command as an array of strings
  "workdir": "/work",                               // working directory inside the container
  "stdout": "/outputs/result.txt",                  // capture stdout to this path
  "stderr": "/logs/error.txt",                      // capture stderr to this path
  "env": {                                          // environment variables
    "THREADS": "4"
  }
}
```

> **Important:** The command must be given as a JSON array of strings, not as a single shell string. Write `["samtools", "flagstat", "file.bam"]` not `"samtools flagstat file.bam"`. This avoids shell injection and ensures the arguments are passed correctly.

### Tags

Tags are free-form key/value string pairs that carry metadata about the task. Some TES platforms use specific tags to route tasks to particular compute queues or associate them with projects:

```json
{
  "project": "DPUK_Project55",   // which project this task belongs to
  "tres": "DPUK"                 // which compute queue/resource group to target
}
```

The meaning of tags is platform-specific — ask your TES administrator which tags, if any, are meaningful in your environment.

---

## 5. Using this library

### 5.1 Installation

Install with pip:

```bash
pip install tes-client
```

Or install directly from the repository:

```bash
pip install git+https://github.com/SwanseaUniversityMedical/tes_client.git
```

Requires Python 3.11 or later.

---

### 5.2 Authentication

Before you can submit tasks, you need to authenticate with the TES server. This library supports four authentication methods. They are interchangeable — once you have created an `auth` object, the rest of the code is identical regardless of which method you use.

#### Option A: No authentication

Use this for open TES endpoints, local development servers, or any endpoint that does not require credentials.

```python
from tes_client import NoAuth

auth = NoAuth()
```

#### Option B: Client credentials (machine-to-machine)

This is the right choice for **automated scripts and pipelines**. You use a service account registered in Keycloak — no browser, no user interaction. The library fetches a token automatically and silently renews it when it expires.

```python
from tes_client import ClientCredentialsAuth

auth = ClientCredentialsAuth(
    base_url="https://keycloak.example.org",   # your Keycloak server URL
    realm="my-realm",                           # the Keycloak realm name
    client_id="tes-service-account",            # the service account client ID
    client_secret="super-secret",               # the client secret from Keycloak
)
```

Ask your system administrator for the `base_url`, `realm`, `client_id`, and `client_secret` values.

#### Option C: Username and password

Good for **interactive use** — you provide your username and password once and the library manages the session from there. The library fetches a token using your credentials and then uses the refresh token silently for subsequent calls.

```python
from tes_client import PasswordAuth

auth = PasswordAuth(
    base_url="https://keycloak.example.org",
    realm="my-realm",
    client_id="tes-client",
    username="alice",
    password="hunter2",
    # client_secret="...",  # only needed if your Keycloak client is confidential
)
```

> **Note:** The ROPC grant (username/password) must be enabled on the Keycloak realm. If it doesn't work, ask your administrator.

#### Option D: Browser login (Authorization Code + PKCE)

The most secure option for interactive sessions. The first time you make a request, a browser window opens and you log in through the normal Keycloak login page. After that, your session is maintained silently until the refresh token expires, at which point the browser opens again. No client secret is needed.

```python
from tes_client import AuthorizationCodeAuth

auth = AuthorizationCodeAuth(
    base_url="https://keycloak.example.org",
    realm="my-realm",
    client_id="tes-public-client",  # must be a public client registered in Keycloak
    redirect_port=8080,             # must match the redirect URI configured in Keycloak
)
```

The browser opens the first time you make an API call (e.g. `client.submit(...)`), not when you create the `auth` object. You can trigger it early with `auth.ensure_valid()`.

#### Checking and refreshing tokens in long-running scripts

All auth classes provide two helper methods for managing token lifetime in scripts that run for a long time:

```python
# Check whether the token is still valid — instant, no network call
if auth.is_valid():
    print("Good to go.")
else:
    print("Token has expired.")

# Refresh the token now if it has expired — silent no-op if it's still valid
auth.ensure_valid()
```

Use `ensure_valid()` at the start of each loop iteration in batch scripts:

```python
for sample in samples:
    auth.ensure_valid()          # silently refreshes if needed; no-op otherwise
    task_id = client.submit(build_task(sample))
```

---

### 5.3 Building a task

Tasks are built using the `TesTask` class and a **fluent API** — each builder method returns the task object itself, so you can chain calls together in one readable block.

#### Minimal task: just run a command

```python
from tes_client import TesTask

task = (
    TesTask(name="hello-world")
    .add_executor(
        image="ubuntu",
        command=["echo", "Hello from TES!"],
    )
)
```

The only thing a task must have is at least one executor.

#### Inspect the task before submitting

Before submitting, you can print the exact JSON that would be sent to the server. This is very useful for checking your task is correct:

```python
print(task.submission_json())
```

```json
{
  "name": "hello-world",
  "executors": [
    {
      "image": "ubuntu",
      "command": ["echo", "Hello from TES!"]
    }
  ]
}
```

#### Full task with inputs, outputs, resources, and tags

```python
task = (
    TesTask(name="samtools-flagstat")
    .set_project_tag("DPUK_Project55")       # tags["project"] = "DPUK_Project55"
    .set_tres_tag("DPUK")                    # tags["tres"] = "DPUK"
    .add_input(
        url="s3://my-bucket/data/sample.bam",
        path="/inputs/sample.bam",
    )
    .add_output(
        path="/outputs/sample.flagstat",
        url="s3://my-bucket/results/sample.flagstat",
    )
    .set_resources(cpu_cores=2, ram_gb=4, disk_gb=20)
    .add_executor(
        image="biocontainers/samtools:1.18",
        command=["samtools", "flagstat", "/inputs/sample.bam"],
        stdout="/outputs/sample.flagstat",    # capture stdout to this output file
    )
)

print(task.submission_json())
```

#### Builder methods reference

| Method | What it does |
|---|---|
| `TesTask(name=..., description=...)` | Create a new task with optional name and description |
| `.add_executor(image, command, ...)` | Add a Docker container and command to run |
| `.add_input(path, url=None, ...)` | Add an input file to stage into the container |
| `.add_output(path, url, ...)` | Add an output file to stage out after execution |
| `.set_resources(cpu_cores, ram_gb, disk_gb, ...)` | Set compute resource requirements |
| `.set_project_tag(project)` | Set `tags["project"]` |
| `.set_tres_tag(tres)` | Set `tags["tres"]` |
| `.submission_json()` | Return the task as a JSON string (as it would be sent to the API) |
| `.submission_dict()` | Return the task as a Python dictionary |

#### `add_executor` parameters

| Parameter | Required? | Description |
|---|---|---|
| `image` | Yes | Docker image name (e.g. `"ubuntu"`, `"python:3.11"`, `"biocontainers/samtools:1.18"`) |
| `command` | Yes | Command as a list of strings: `["samtools", "flagstat", "/inputs/in.bam"]` |
| `workdir` | No | Working directory inside the container |
| `stdout` | No | Redirect stdout to this path inside the container |
| `stderr` | No | Redirect stderr to this path inside the container |
| `env` | No | Dictionary of environment variables: `{"THREADS": "4"}` |
| `ignore_error` | No | If `True`, continue to the next executor even if this one fails |

#### `add_input` parameters

| Parameter | Required? | Description |
|---|---|---|
| `path` | Yes | Path inside the container where the file will appear |
| `url` | No | URL to fetch the file from (e.g. `s3://...`, `http://...`) |
| `content` | No | Inline text content (alternative to `url` for small text files) |
| `type` | No | `"FILE"` (default) or `"DIRECTORY"` |
| `name` | No | Human-readable name |
| `description` | No | Description |

#### `add_output` parameters

| Parameter | Required? | Description |
|---|---|---|
| `path` | Yes | Path inside the container where the output file will be written |
| `url` | Yes | Destination URL to upload the file to after execution |
| `type` | No | `"FILE"` (default) or `"DIRECTORY"` |

#### `set_resources` parameters

| Parameter | Description |
|---|---|
| `cpu_cores` | Number of CPU cores |
| `ram_gb` | RAM in gigabytes |
| `disk_gb` | Local disk space in gigabytes |
| `preemptible` | Whether the task can run on preemptible/spot instances |
| `zones` | List of preferred zones (cloud platforms) |
| `backend_parameters` | Platform-specific key/value parameters |

#### Multiple executors

TES supports running more than one container sequentially within a single task. This is useful for pipelines with a fixed sequence of steps:

```python
task = (
    TesTask(name="index-then-count")
    .add_input(url="s3://my-bucket/sample.bam", path="/inputs/sample.bam")
    .add_output(path="/outputs/sample.bai", url="s3://my-bucket/sample.bai")
    .add_output(path="/outputs/sample.flagstat", url="s3://my-bucket/sample.flagstat")

    # Step 1: index the BAM file
    .add_executor(
        image="biocontainers/samtools:1.18",
        command=["samtools", "index", "/inputs/sample.bam", "/outputs/sample.bai"],
    )
    # Step 2: count reads — runs only after step 1 completes
    .add_executor(
        image="biocontainers/samtools:1.18",
        command=["samtools", "flagstat", "/inputs/sample.bam"],
        stdout="/outputs/sample.flagstat",
    )
)
```

If step 1 fails, step 2 does not run and the task moves to `EXECUTOR_ERROR`.

---

### 5.4 Connecting to a TES server

`TesClient` is the object that handles all communication with the TES server. Create one by passing the server URL and your auth object:

```python
from tes_client import TesClient

client = TesClient(
    tes_url="https://tes.example.org",
    token_manager=auth,           # the auth object you created in step 5.2
    timeout=60.0,                 # HTTP timeout in seconds (default: 60)
)
```

#### Checking the server is reachable

`service_info()` calls the `/v1/service-info` endpoint and returns metadata about the TES server. It's a useful sanity check before submitting anything:

```python
import json

info = client.service_info()
print(json.dumps(info, indent=2))
```

If this raises an error, the server URL is wrong, the server is not running, or you have a network connectivity issue.

---

### 5.5 Submitting and tracking a task

#### Submitting

`client.submit(task)` sends the task to the server and immediately returns a **task ID** — a unique identifier the server assigns to this run. Your Python process does not wait for the task to finish; submission is instant.

```python
task_id = client.submit(task)
print(f"Submitted: {task_id}")
# e.g. "Submitted: a1b2c3d4-e5f6-..."
```

Save this ID — you need it to check status, fetch logs, or cancel the task.

#### Tracking to completion

`TaskTracker` handles the polling loop for you. It periodically fetches the task state, prints each state transition, and blocks until the task reaches a terminal state:

```python
from tes_client import TaskTracker

tracker = TaskTracker(
    client,
    task_id,
    poll_interval=15.0,   # how often to check, in seconds
)

# wait() blocks until the task finishes (or timeout is reached)
final = tracker.wait(timeout=3600)  # give up after 1 hour

print(f"Final state: {final.state}")
```

While waiting, you will see output like:

```
[TES a1b2c3d4] QUEUED
[TES a1b2c3d4] INITIALIZING
[TES a1b2c3d4] RUNNING
[TES a1b2c3d4] COMPLETE
```

#### Reacting to the outcome

```python
if final.state.is_failure():
    print(f"Task did not complete successfully: {final.state}")
    tracker.report()      # prints detailed logs: exit codes, stderr, system messages
else:
    print("Task completed successfully!")
```

`is_failure()` returns `True` for `EXECUTOR_ERROR`, `SYSTEM_ERROR`, and `CANCELED`. `is_terminal()` returns `True` for those plus `COMPLETE`.

#### Custom state-change callback

If you want to do something on each state change — send a notification, write to a log file, update a database — pass a callback function:

```python
import datetime

def on_state_change(task):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] Task moved to: {task.state}")
    # could also: send a message, write to a database, trigger another job, etc.

tracker = TaskTracker(
    client,
    task_id,
    poll_interval=15.0,
    on_state_change=on_state_change,
)
final = tracker.wait(timeout=3600)
```

#### Quick state check (without waiting)

If you just want the current state without blocking:

```python
state = client.state(task_id)
print(state)            # e.g. TesState.RUNNING
print(state.value)      # e.g. "RUNNING"
```

---

### 5.6 Other useful operations

#### Fetch a task by ID

```python
# Minimal view — fast, just state and name
task = client.get(task_id)
print(task.state)
print(task.name)

# Full view — includes executor logs, stdout/stderr, exit codes
task = client.get(task_id, full=True)
if task.logs:
    for attempt in task.logs:
        for executor_log in attempt.logs:
            print(f"Exit code: {executor_log.exit_code}")
            if executor_log.stderr:
                print(f"Stderr:\n{executor_log.stderr}")
```

#### List tasks

```python
# List the most recent tasks
tasks = client.list_tasks(page_size=20)
for t in tasks:
    print(f"{t.id}  {t.state}  {t.name or '(unnamed)'}")

# Filter by name prefix
my_tasks = client.list_tasks(name_prefix="samtools-", page_size=50)
```

#### Cancel a running task

```python
client.cancel(task_id)
```

The task moves to `CANCELING` and then `CANCELED`. Note that cancellation is a request — depending on the TES platform, the task may still run for a short time before being stopped.

#### Print a detailed status report

```python
tracker = TaskTracker(client, task_id)
tracker.report()
```

This prints the task state, name, creation time, and — if logs are available — executor exit codes, stderr output, and any system messages from the TES platform.

---

### 5.7 Error handling

#### Network errors

If the server is unreachable or returns an HTTP error, the client raises an `httpx` exception:

```python
import httpx

try:
    task_id = client.submit(task)

except httpx.ConnectError:
    print("Could not connect to the TES server.")
    print("Check the URL and your network connection.")

except httpx.HTTPStatusError as e:
    code = e.response.status_code
    print(f"Server returned HTTP {code}")
    if code == 401:
        print("Authentication failed — check your credentials.")
    elif code == 403:
        print("Authorised but not permitted — check your Keycloak roles.")
    elif code == 400:
        print("Bad request — check your task JSON with task.submission_json()")
        print(e.response.text)
```

#### Timeout waiting for a task

`tracker.wait()` raises `TimeoutError` if the task has not finished within the specified number of seconds:

```python
try:
    final = tracker.wait(timeout=3600)
except TimeoutError:
    current = client.state(task_id)
    print(f"Timed out. Task is still in state: {current}")
    client.cancel(task_id)   # optionally cancel the task
```

#### Task failures

A task that fails at the executor level (your command exited non-zero) has state `EXECUTOR_ERROR`. A platform-level failure (infrastructure issue) has state `SYSTEM_ERROR`. Use `tracker.report()` to see the exit codes and stderr output:

```python
final = tracker.wait(timeout=3600)

if final.state == TesState.EXECUTOR_ERROR:
    print("The command failed. Check the logs:")
    tracker.report()
elif final.state == TesState.SYSTEM_ERROR:
    print("A platform error occurred. Contact your administrator.")
    tracker.report()
```

---

## 6. Complete worked examples

### Example 1: Minimal script (no authentication)

The simplest possible end-to-end usage — submit a task to an open endpoint and wait for it:

```python
from tes_client import NoAuth, TesClient, TesTask, TaskTracker

# Connect
auth = NoAuth()
client = TesClient(tes_url="http://your-tes-server:8000", token_manager=auth)

# Build
task = (
    TesTask(name="hello-world")
    .add_executor(image="ubuntu", command=["echo", "Hello from TES!"])
)

# Submit and track
task_id = client.submit(task)
print(f"Submitted: {task_id}")

tracker = TaskTracker(client, task_id, poll_interval=10.0)
final = tracker.wait(timeout=600)

if final.state.is_failure():
    tracker.report()
else:
    print("Done!")
```

---

### Example 2: Bioinformatics pipeline with authentication

A realistic example: run `samtools flagstat` on a BAM file stored in object storage, using a Keycloak service account:

```python
from tes_client import ClientCredentialsAuth, TesClient, TesTask, TaskTracker

# Authenticate with a Keycloak service account
auth = ClientCredentialsAuth(
    base_url="https://keycloak.example.org",
    realm="research-realm",
    client_id="pipeline-service-account",
    client_secret="my-secret",
)

# Connect
client = TesClient(tes_url="https://tes.example.org", token_manager=auth)

# Build the task
task = (
    TesTask(name="flagstat-sample-001")
    .set_project_tag("DPUK_Project55")
    .set_tres_tag("DPUK")
    .add_input(
        url="s3://research-data/samples/sample_001.bam",
        path="/inputs/sample.bam",
    )
    .add_output(
        path="/outputs/sample.flagstat",
        url="s3://research-results/sample_001.flagstat",
    )
    .set_resources(cpu_cores=2, ram_gb=4, disk_gb=10)
    .add_executor(
        image="biocontainers/samtools:1.18",
        command=["samtools", "flagstat", "/inputs/sample.bam"],
        stdout="/outputs/sample.flagstat",
    )
)

# Preview the JSON before submitting (useful for debugging)
print("Task to submit:")
print(task.submission_json())

# Submit
auth.ensure_valid()
task_id = client.submit(task)
print(f"\nSubmitted: {task_id}")

# Track
tracker = TaskTracker(client, task_id, poll_interval=30.0)
final = tracker.wait(timeout=3600)

if final.state.is_failure():
    print(f"Task failed: {final.state}")
    tracker.report()
else:
    print("Analysis complete.")
```

---

### Example 3: Batch submission with token management

Submit many tasks in a loop, with proactive token refresh:

```python
from tes_client import PasswordAuth, TesClient, TesTask, TaskTracker

auth = PasswordAuth(
    base_url="https://keycloak.example.org",
    realm="my-realm",
    client_id="tes-client",
    username="alice",
    password="hunter2",
)

client = TesClient(tes_url="https://tes.example.org", token_manager=auth)

samples = ["sample_001", "sample_002", "sample_003", "sample_004"]
task_ids = []

for sample in samples:
    # Proactively refresh the token before each submission
    if not auth.is_valid():
        print(f"Token expired — re-authenticating before processing {sample}…")
    auth.ensure_valid()

    task = (
        TesTask(name=f"flagstat-{sample}")
        .set_project_tag("DPUK_Project55")
        .add_input(
            url=f"s3://research-data/samples/{sample}.bam",
            path="/inputs/sample.bam",
        )
        .add_output(
            path="/outputs/result.flagstat",
            url=f"s3://research-results/{sample}.flagstat",
        )
        .set_resources(cpu_cores=2, ram_gb=4)
        .add_executor(
            image="biocontainers/samtools:1.18",
            command=["samtools", "flagstat", "/inputs/sample.bam"],
            stdout="/outputs/result.flagstat",
        )
    )

    task_id = client.submit(task)
    task_ids.append((sample, task_id))
    print(f"  {sample} → {task_id}")

print(f"\nAll {len(task_ids)} tasks submitted. Waiting for completion…\n")

# Wait for each task in order
for sample, task_id in task_ids:
    tracker = TaskTracker(client, task_id, poll_interval=30.0)
    final = tracker.wait(timeout=7200)
    status = "OK" if not final.state.is_failure() else f"FAILED ({final.state})"
    print(f"  {sample}: {status}")
```

---

### Example 4: Browser login for interactive use

For notebooks or interactive sessions where browser-based login is acceptable:

```python
from tes_client import AuthorizationCodeAuth, TesClient, TesTask, TaskTracker

# The browser will open the first time a request is made
auth = AuthorizationCodeAuth(
    base_url="https://keycloak.example.org",
    realm="my-realm",
    client_id="tes-public-client",
    redirect_port=8080,
)

client = TesClient(tes_url="https://tes.example.org", token_manager=auth)

# This will open the browser for login
print("Connecting to TES server (browser may open for login)…")
info = client.service_info()
print(f"Connected to: {info.get('name', 'TES server')}")

task = (
    TesTask(name="interactive-analysis")
    .set_project_tag("my-project")
    .add_executor(image="python:3.11", command=["python", "-c", "print('hello')"])
)

task_id = client.submit(task)
tracker = TaskTracker(client, task_id)
final = tracker.wait(timeout=600)
print(f"Result: {final.state}")
```

---

## 7. Quick reference

### Imports

```python
from tes_client import (
    NoAuth,
    ClientCredentialsAuth,
    PasswordAuth,
    AuthorizationCodeAuth,
    TesClient,
    TesTask,
    TesState,
    TaskTracker,
)
```

### Auth objects

| Class | Use case |
|---|---|
| `NoAuth()` | Open/dev endpoints — no credentials needed |
| `ClientCredentialsAuth(base_url, realm, client_id, client_secret)` | Automated scripts / service accounts |
| `PasswordAuth(base_url, realm, client_id, username, password)` | Interactive user, no browser |
| `AuthorizationCodeAuth(base_url, realm, client_id, redirect_port)` | Interactive user, browser login |

All auth classes: `auth.is_valid()`, `auth.ensure_valid()`, `auth.auth_header()`

### TesClient methods

| Method | Returns | Description |
|---|---|---|
| `client.submit(task)` | `str` | Submit a task; returns the server-assigned task ID |
| `client.get(task_id, full=False)` | `TesTask` | Fetch a task by ID; `full=True` includes logs |
| `client.state(task_id)` | `TesState` | Get the current state of a task |
| `client.list_tasks(name_prefix, page_size, page_token, view)` | `list[TesTask]` | List tasks with optional filtering |
| `client.cancel(task_id)` | `None` | Request cancellation |
| `client.service_info()` | `dict` | Get server metadata |

### TesTask builder methods

| Method | Description |
|---|---|
| `TesTask(name, description)` | Create a new task |
| `.add_executor(image, command, workdir, stdout, stderr, env)` | Add a container/command |
| `.add_input(path, url, content, type)` | Add an input file |
| `.add_output(path, url, type)` | Add an output file |
| `.set_resources(cpu_cores, ram_gb, disk_gb, preemptible, zones)` | Set resource requirements |
| `.set_project_tag(project)` | Set `tags["project"]` |
| `.set_tres_tag(tres)` | Set `tags["tres"]` |
| `.submission_json(indent=2)` | Preview or print the submission JSON |
| `.submission_dict()` | Get the submission as a Python dict |

### Task states

| State | Terminal? | Failure? |
|---|---|---|
| `QUEUED` | No | No |
| `INITIALIZING` | No | No |
| `RUNNING` | No | No |
| `PAUSED` | No | No |
| `COMPLETE` | Yes | No |
| `EXECUTOR_ERROR` | Yes | Yes |
| `SYSTEM_ERROR` | Yes | Yes |
| `CANCELED` | Yes | Yes |
| `CANCELING` | No | No |

`state.is_terminal()` — True for COMPLETE, EXECUTOR_ERROR, SYSTEM_ERROR, CANCELED  
`state.is_failure()` — True for EXECUTOR_ERROR, SYSTEM_ERROR, CANCELED

### TaskTracker

```python
tracker = TaskTracker(
    client,
    task_id,
    poll_interval=10.0,        # seconds between checks (default: 10)
    on_state_change=callback,  # optional: called on each state change
)

final = tracker.wait(timeout=3600)   # raises TimeoutError if exceeded
tracker.report()                     # print a one-shot status report
```
