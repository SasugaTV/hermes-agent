"""Thin wrapper around the Google Tasks API (OAuth installed-app flow).

Talks directly to Google with credentials you create yourself in Google
Cloud Console (see README.md) - nothing here relays your data through any
third-party server. ``token.json`` is the refresh token cache; after the
first interactive auth, subsequent runs are non-interactive.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/tasks"]


def millis_to_due(millis: int) -> str:
    """Kanban ``dueDate`` (epoch ms) -> Google Tasks ``due`` (date-only RFC3339).

    Google Tasks ignores the time-of-day component of ``due``, so this
    always emits midnight UTC.
    """
    d = dt.datetime.fromtimestamp(millis / 1000, tz=dt.timezone.utc).date()
    return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).strftime(
        "%Y-%m-%dT00:00:00.000Z"
    )


def due_to_millis(due: str) -> int:
    """Google Tasks ``due`` -> Kanban ``dueDate`` (epoch ms, midnight UTC)."""
    d = dt.datetime.strptime(due[:10], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


def get_credentials(credentials_path: str, token_path: str) -> Credentials:
    creds: Optional[Credentials] = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    return creds


class GoogleTasksClient:
    def __init__(self, credentials_path: str, token_path: str):
        creds = get_credentials(credentials_path, token_path)
        self.service = build("tasks", "v1", credentials=creds)

    def ensure_tasklist(self, title: str) -> str:
        page_token = None
        while True:
            resp = (
                self.service.tasklists()
                .list(maxResults=100, pageToken=page_token)
                .execute()
            )
            for tl in resp.get("items", []):
                if tl["title"] == title:
                    return tl["id"]
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        created = self.service.tasklists().insert(body={"title": title}).execute()
        return created["id"]

    def list_tasks(self, tasklist_id: str) -> list[dict]:
        tasks: list[dict] = []
        page_token = None
        while True:
            resp = (
                self.service.tasks()
                .list(
                    tasklist=tasklist_id,
                    showHidden=True,
                    showCompleted=True,
                    maxResults=100,
                    pageToken=page_token,
                )
                .execute()
            )
            tasks.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return tasks

    def get_task(self, tasklist_id: str, task_id: str) -> Optional[dict]:
        try:
            return self.service.tasks().get(tasklist=tasklist_id, task=task_id).execute()
        except HttpError as e:
            if e.resp.status == 404:
                return None
            raise

    def insert_task(self, tasklist_id: str, body: dict) -> dict:
        return self.service.tasks().insert(tasklist=tasklist_id, body=body).execute()

    def patch_task(self, tasklist_id: str, task_id: str, body: dict) -> dict:
        return (
            self.service.tasks()
            .patch(tasklist=tasklist_id, task=task_id, body=body)
            .execute()
        )

    def delete_task(self, tasklist_id: str, task_id: str) -> None:
        self.service.tasks().delete(tasklist=tasklist_id, task=task_id).execute()
