"""One place that builds the GitHub client for the notes repo.

Uses a GitHub App (a bot owned by the org) when GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY are set,
and falls back to GITHUB_TOKEN otherwise, so you can switch over and test before removing the token.

Settings (.env or the host's environment):
    GITHUB_NOTES_REPO         owner/repo of the private notes repo, e.g. ask-inglis/ask_inglis_notes
    GITHUB_APP_ID             the app's numeric ID (shown on the app's settings page)
    GITHUB_APP_PRIVATE_KEY    the private key text (the whole PEM). In .env or a host setting, newlines
                              may be written as \\n; they are converted here.
    GITHUB_APP_INSTALLATION_ID  optional; looked up automatically from the repo if left out
    GITHUB_TOKEN              the old personal token (fallback only)
"""
import os
import threading

from github import Auth, Github, GithubIntegration

_lock = threading.Lock()
_client = None
_mode = None


def _private_key() -> str:
    key = os.environ.get("GITHUB_APP_PRIVATE_KEY", "")
    return key.replace("\\n", "\n").strip()


def _build_client():
    repo_name = os.environ.get("GITHUB_NOTES_REPO", "")
    app_id, key = os.environ.get("GITHUB_APP_ID"), _private_key()
    if app_id and key:
        app_auth = Auth.AppAuth(int(app_id), key)
        installation_id = os.environ.get("GITHUB_APP_INSTALLATION_ID")
        if installation_id:
            installation_id = int(installation_id)
        else:
            owner, name = repo_name.split("/", 1)
            installation_id = GithubIntegration(auth=app_auth).get_repo_installation(owner, name).id
        # AppInstallationAuth fetches short-lived tokens itself and renews them when they expire
        return Github(auth=Auth.AppInstallationAuth(app_auth, installation_id)), "app"
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return Github(auth=Auth.Token(token)), "token"
    return None, None


def get_github_client():
    """Returns a Github client, or None if nothing is configured."""
    global _client, _mode
    with _lock:
        if _client is None:
            try:
                _client, _mode = _build_client()
                if _client is not None:
                    print(f"GitHub access: using {'the GitHub App' if _mode == 'app' else 'a personal token'}")
            except Exception as e:
                print(f"GitHub setup failed: {e}")
                _client, _mode = None, None
        return _client


def get_notes_repo():
    """The private notes repo object, or None if GitHub isn't configured or can't be reached."""
    client = get_github_client()
    name = os.environ.get("GITHUB_NOTES_REPO")
    if client is None or not name:
        return None
    try:
        return client.get_repo(name)
    except Exception as e:
        print(f"Could not open {name}: {e}")
        return None