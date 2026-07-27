# librekanban-google-sync

A small standalone tool that syncs a [Libre Kanban](https://play.google.com/store/apps/details?id=com.buenhijogames.mikanban)
board (Android app) with **Google Tasks**, using Libre Kanban's own
export/import files. It does not modify Libre Kanban itself, does not
touch any other part of this repository, and talks directly to Google's
API with credentials you create yourself - no third-party server is
involved.

## The workflow

This is a **manual, one-direction-at-a-time** sync, not a live/automatic
one:

1. On your phone, export your board from Libre Kanban (`.librekanbanbackup`
   or `.mikanban`) and get the file onto the machine running this tool
   (Drive, email, USB, Syncthing, whatever you already use).
2. Run `sync.py export-to-google` to push any new/changed cards up to a
   Google Tasks list.
3. Later (after ticking things off or adding tasks directly in Google
   Tasks/Calendar), run `sync.py import-from-google --output synced.json`
   to pull those changes into a new board file.
4. Manually import `synced.json` back into Libre Kanban on your phone.
5. Repeat.

Because it's file-based and manual, there's no server component and no
background daemon - you run it whenever you want to sync.

## How a card maps to a Google Task

You chose Google Tasks (not Calendar events), so:

- Each Kanban **board** becomes one Google Tasks **list** (created
  automatically, named by `google_tasklist_title` in config).
- Each **card** becomes one **Google Task**: title, description, and due
  date map directly. Google Tasks has no concept of columns, tags,
  priority, or story points, so those are preserved in a small
  machine-readable block appended to the task's notes (see
  `librekanban_sync/notes_codec.py`) - this is what lets
  `import-from-google` reconstruct the full card later.
- Whether a card counts as "done" is decided by its **column name**:
  columns listed in `done_column_names` (default `Finished`, `Done`,
  `Completed`) map to a completed Google Task; everything else maps to
  `needsAction`. Toggling a task's completed checkbox in Google Tasks and
  then running `import-from-google` moves the card to the first
  configured done column (or to `reopened_column_name` if you
  un-complete it).
- New tasks added directly in Google Tasks (with no matching card) get
  created as new cards in `new_task_column_name` on the next
  `import-from-google` run.

**Caveat:** the export format was reverse-engineered from a single real
export file, which had no tags on its one task, so the field Libre Kanban
uses to link a task to its tags is unconfirmed. `task_tag_names()` in
`librekanban_sync/librekanban_file.py` checks a couple of plausible
shapes and falls back to "no tags" rather than guessing wrong - if tags
aren't round-tripping for you, check what key your export actually uses
there.

## Setup

### 1. Install dependencies

```bash
cd librekanban-google-sync
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create a Google OAuth client (one-time)

This tool needs its own OAuth client so it can talk to your Google
account directly - there's no shared/hosted credential.

1. Go to the [Google Cloud Console](https://console.cloud.google.com/),
   create a project (or reuse one).
2. Under **APIs & Services > Library**, enable the **Google Tasks API**.
3. Under **APIs & Services > OAuth consent screen**, configure it as
   **External**, keep it in **Testing** mode, and add your own Google
   account as a test user. (Testing mode is fine for personal use - it
   just means the consent screen shows an "unverified app" warning you
   click through.)
4. Under **APIs & Services > Credentials**, create an **OAuth client ID**
   of type **Desktop app**. Download the JSON and save it as
   `credentials.json` in this directory (or point
   `google_credentials_path` in your config at wherever you put it).

### 3. Configure

```bash
cp config.example.json config.json
```

Edit `config.json`:
- `librekanban_export_path`: where the Libre Kanban export file lives on
  this machine.
- `board_name`: which board to sync, if the file contains more than one.
  Run `python sync.py list-boards <file>` to see board names and columns
  in a given export without needing Google auth.
- `done_column_names` / `reopened_column_name` / `new_task_column_name`:
  match these to your board's actual column names.

### 4. First run (interactive auth)

The first `export-to-google` or `import-from-google` run needs to
complete an OAuth flow in a browser, after which `token.json` caches a
refresh token and every later run is non-interactive.

- **Machine with a browser:** just run the command; it opens a browser
  tab automatically.
- **Headless home server:** forward a local port over SSH before running
  it, e.g. `ssh -L 8080:localhost:8080 you@homeserver`, then run the
  command on the server and open the URL it prints in your own machine's
  browser. Once `token.json` exists, later runs don't need this.

## Usage

```bash
# See what's in an export file (no auth needed)
python sync.py list-boards /path/to/backup.librekanbanbackup.json

# Push local Kanban changes up to Google Tasks
python sync.py export-to-google

# Pull Google Tasks changes into a new file to import into Libre Kanban
python sync.py import-from-google --output synced_board.json
```

Both commands print a JSON summary of what happened (created / updated /
unchanged / orphaned / removed counts).

## Deletion policy

Deleting a card in Kanban or a task in Google doesn't automatically
delete the other side by default - `on_kanban_delete` and
`on_google_delete` in `config.json` default to `"leave"` so nothing is
destroyed without you opting in. Set them to `"delete"` (or
`"complete"` for `on_kanban_delete`) if you'd rather deletions
propagate.

## Running the tests

```bash
pip install pytest
pytest librekanban-google-sync/tests
```

The tests use a real sample export (`tests/fixtures/sample_backup.json`)
and a fake in-memory Google Tasks client, so they don't need network
access or real credentials.
