"""End-to-end examples: all three auth flows, then submit + track a TES task."""

from tes_client import ClientCredentialsAuth, TaskTracker, TesClient, TesTask, NoAuth

TES_URL = "http://192.168.99.53:8000"
KC_URL = "https://keycloak.example.org"
REALM = "my-realm"

# ------------------------------------------------------------------ #
# Option 1: Client credentials (machine-to-machine, no browser)       #
# ------------------------------------------------------------------ #

# auth = ClientCredentialsAuth(
#     base_url=KC_URL,
#     realm=REALM,
#     client_id="tes-client",
#     client_secret="my-client-secret",
# )

# ------------------------------------------------------------------ #
# Option 2: Username + password (ROPC)                                 #
# Gets a refresh token — subsequent calls are silent until it expires. #
# ------------------------------------------------------------------ #

# from tes_client import PasswordAuth
# auth = PasswordAuth(
#     base_url=KC_URL,
#     realm=REALM,
#     client_id="tes-client",
#     username="alice",
#     password="hunter2",
#     client_secret="optional-if-confidential-client",
# )

# ------------------------------------------------------------------ #
# Option 3: Authorization code + PKCE (browser pop-up)                #
# On first use opens a browser; subsequent calls use the refresh token #
# silently until it too expires, then re-opens the browser.           #
# ------------------------------------------------------------------ #

# from tes_client import AuthorizationCodeAuth
# auth = AuthorizationCodeAuth(
#     base_url=KC_URL,
#     realm=REALM,
#     client_id="tes-public-client",   # public client → no client_secret needed
#     redirect_port=8080,              # must match the redirect URI in Keycloak
#     scope="openid",
# )

# ------------------------------------------------------------------ #
# Client — same regardless of auth method                             #
# ------------------------------------------------------------------ #

auth = NoAuth()
client = TesClient(tes_url=TES_URL, token_manager=auth)

# ------------------------------------------------------------------ #
# Build the task with the fluent builder                               #
# ------------------------------------------------------------------ #

task = (
    TesTask(name="my-analysis2")
    .set_project_tag("DPUK_Project55")
    .set_tres_tag("DPUK")
    .add_executor(
        image="ubuntu",
        command=["echo", "Hello World"],
    )
)

# ------------------------------------------------------------------ #
# Show the TES message created                                         #
# ------------------------------------------------------------------ #

print(task.submission_json())

# ------------------------------------------------------------------ #
# Optionally check / refresh auth before submitting                    #
# ------------------------------------------------------------------ #

if not auth.is_valid():
    print("Token expired — re-authenticating…")
auth.ensure_valid()   # no-op if already valid; re-auths silently if not

# ------------------------------------------------------------------ #
# Submit and track                                                     #
# ------------------------------------------------------------------ #

task_id = client.submit(task)
print(f"Submitted: {task_id}")

tracker = TaskTracker(client, task_id, poll_interval=15.0)
final = tracker.wait(timeout=3600)

if final.state.is_failure():
    print(f"Task failed with state: {final.state}")
    tracker.report()
else:
    print("Task completed successfully.")
