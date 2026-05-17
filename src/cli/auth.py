"""
techpulse - Auth module
Handles user session and Supabase client instantiation.
"""
import json
import os
from pathlib import Path
from typing import Dict, Any, Tuple

import typer
import keyring
from rich import print as rprint

SERVICE_NAME = "techpulse-ai"
CONFIG_PATH = Path.home() / ".techpulse" / "config.json"

def _load_session() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        rprint("[red]Not logged in. Run: pulse login[/red]")
        raise typer.Exit(1)

    with open(CONFIG_PATH) as f:
        session = json.load(f)

    try:
        session["access_token"] = keyring.get_password(SERVICE_NAME, f"{session['user_id']}_access")
        session["refresh_token"] = keyring.get_password(SERVICE_NAME, f"{session['user_id']}_refresh")
    except Exception:
        pass

    if not session.get("access_token"):
        rprint("[red]Session credentials missing. Please login again.[/red]")
        raise typer.Exit(1)

    return session

def _save_session(data: Dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    uid = data["user_id"]

    keyring_success = False
    try:
        keyring.set_password(SERVICE_NAME, f"{uid}_access", data["access_token"])
        keyring.set_password(SERVICE_NAME, f"{uid}_refresh", data["refresh_token"])
        keyring_success = True
    except Exception:
        pass

    meta = {
        "user_id": uid,
        "email": data["email"],
        "anon_key": data["anon_key"],
    }
    if not keyring_success:
        meta["access_token"] = data["access_token"]
        meta["refresh_token"] = data["refresh_token"]
        meta["vault_type"] = "file"
    else:
        meta["vault_type"] = "system"

    with open(CONFIG_PATH, "w") as f:
        json.dump(meta, f, indent=2)
    os.chmod(CONFIG_PATH, 0o600)

def _clear_session() -> None:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                session = json.load(f)
                uid = session.get("user_id")
                if uid:
                    keyring.delete_password(SERVICE_NAME, f"{uid}_access")
                    keyring.delete_password(SERVICE_NAME, f"{uid}_refresh")
        except Exception:
            pass
        CONFIG_PATH.unlink()

def get_user_client() -> Tuple[Any, Dict[str, Any]]:
    from supabase import create_client
    session = _load_session()
    from shared.config import settings

    client = create_client(settings.supabase_url, session["anon_key"])
    try:
        res = client.auth.set_session(session["access_token"], session["refresh_token"])
        if res.session and res.session.access_token != session["access_token"]:
             _save_session({
                "access_token": res.session.access_token,
                "refresh_token": res.session.refresh_token,
                "user_id": session["user_id"],
                "email": session["email"],
                "anon_key": session["anon_key"],
            })
    except Exception:
        rprint("[bold red]Vault Access Error.[/bold red] Your session is no longer valid.")
        rprint("[dim]Please run: [white]pulse login[/white] to re-authenticate.[/dim]")
        _clear_session()
        raise typer.Exit(1)

    return client, session
