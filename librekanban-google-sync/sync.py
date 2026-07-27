#!/usr/bin/env python3
"""CLI for syncing a Libre Kanban board export with Google Tasks.

See README.md for setup (Google Cloud OAuth client, config.json) and the
manual export/sync/import workflow. Subcommands:

  sync.py export-to-google
      Push Kanban-side changes from the configured export file up to
      Google Tasks.

  sync.py import-from-google --output synced_board.json
      Pull Google-side changes down into a new file, ready to manually
      import back into Libre Kanban.

  sync.py list-boards <file>
      List the boards/columns/task counts in a Libre Kanban export -
      no Google auth needed. Useful for picking board_name in config.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from librekanban_sync import librekanban_file as lkf
from librekanban_sync.config import Config
from librekanban_sync.google_tasks import GoogleTasksClient
from librekanban_sync.state import SyncState
from librekanban_sync.sync import export_to_google, import_from_google


def _load(config_path: str) -> tuple[Config, SyncState, GoogleTasksClient]:
    cfg = Config.load(config_path)
    state = SyncState.load(cfg.state_path)
    client = GoogleTasksClient(cfg.google_credentials_path, cfg.google_token_path)
    return cfg, state, client


def cmd_export(args: argparse.Namespace) -> None:
    cfg, state, client = _load(args.config)
    summary = export_to_google(cfg, client, state)
    state.save(cfg.state_path)
    print(json.dumps(summary, indent=2))
    if summary["orphaned"]:
        print(
            f"\n{len(summary['orphaned'])} Google Task(s) left in place for card(s) "
            "deleted from Kanban (on_kanban_delete=leave in config.json).",
            file=sys.stderr,
        )


def cmd_import(args: argparse.Namespace) -> None:
    cfg, state, client = _load(args.config)
    summary = import_from_google(cfg, client, state, args.output)
    state.save(cfg.state_path)
    print(json.dumps(summary, indent=2))
    print(f"\nWrote {args.output} - import this file into Libre Kanban.", file=sys.stderr)


def cmd_list_boards(args: argparse.Namespace) -> None:
    file = lkf.LibreKanbanFile.load(args.file)
    for entry in file.board_entries():
        b = entry["board"]
        print(
            f"{b['name']!r}  id={b['id']}  "
            f"columns={[c['name'] for c in entry['columns']]}  "
            f"tasks={len(entry['tasks'])}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--config", default="config.json", help="path to config.json (default: ./config.json)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export-to-google", help="push Kanban changes up to Google Tasks")
    p_export.set_defaults(func=cmd_export)

    p_import = sub.add_parser(
        "import-from-google", help="pull Google Tasks changes into a new import file"
    )
    p_import.add_argument("--output", required=True, help="path to write the updated board file to")
    p_import.set_defaults(func=cmd_import)

    p_list = sub.add_parser(
        "list-boards",
        help="list boards/columns in a Libre Kanban export file (no Google auth needed)",
    )
    p_list.add_argument("file", help="path to a .librekanbanbackup or .mikanban export")
    p_list.set_defaults(func=cmd_list_boards)

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args.func(args)


if __name__ == "__main__":
    main()
