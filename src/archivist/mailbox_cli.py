"""Private mailbox administration, also usable without the full archive CLI."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import click

from archivist.services.mailbox import MailboxStore, get_mailbox_settings


def _date(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


@click.group()
@click.option("--database", type=click.Path(path_type=Path), help="Use a specific private inbox database.")
@click.pass_context
def mailbox(ctx: click.Context, database: Path | None):
    """Read and clean the private website inbox on its server."""
    try:
        settings = get_mailbox_settings()
        if database is not None:
            settings = replace(settings, database=database.expanduser().resolve())
        ctx.obj = MailboxStore(settings)
    except (OSError, sqlite3.Error, ValueError) as error:
        raise click.ClickException(f"Cannot open mailbox: {error}") from None


@mailbox.command("status")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def status(store: MailboxStore, as_json: bool):
    """Show capacity and unread count."""
    result = store.status()
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False))
    else:
        click.echo(f"Messages: {result['total']}/{result['max_messages']} · unread: {result['unread']} · limit: {result['max_length']} characters")


@mailbox.command("list")
@click.option("--limit", type=click.IntRange(min=1), default=200, show_default=True)
@click.option("--unread", is_flag=True, help="Only show unread messages.")
@click.option("--json", "as_json", is_flag=True, help="Output full messages as JSON.")
@click.pass_obj
def list_messages(store: MailboxStore, limit: int, unread: bool, as_json: bool):
    """List recent messages without marking them read."""
    rows = store.list_messages(limit, unread)
    if as_json:
        click.echo(json.dumps(rows, ensure_ascii=False))
    elif not rows:
        click.echo("Inbox is empty.")
    else:
        for row in rows:
            marker = "*" if row["read_at"] is None else " "
            preview = " ".join(row["message"].split())
            if len(preview) > 90:
                preview = preview[:89] + "…"
            click.echo(f"{marker} {row['id']}  {_date(row['created_at'])}  {row['page']}\n  {preview}")


@mailbox.command("read")
@click.argument("message_id", type=click.IntRange(min=1))
@click.option("--keep-unread", is_flag=True, help="Do not mark the message as read.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def read_message(store: MailboxStore, message_id: int, keep_unread: bool, as_json: bool):
    """Read one message and mark it read."""
    row = store.read_message(message_id, mark_read=not keep_unread)
    if row is None:
        raise click.ClickException("Message not found.")
    if as_json:
        click.echo(json.dumps(row, ensure_ascii=False))
    else:
        click.echo(f"#{row['id']} · {_date(row['created_at'])} · {row['page']}\n\n{row['message']}")


@mailbox.command("delete")
@click.argument("message_ids", nargs=-1, required=True, type=click.IntRange(min=1))
@click.option("--yes", is_flag=True, help="Delete without prompting.")
@click.pass_obj
def delete_messages(store: MailboxStore, message_ids: tuple[int, ...], yes: bool):
    """Permanently delete specific messages to release capacity."""
    if not yes:
        click.confirm(f"Delete {len(set(message_ids))} selected message(s)?", abort=True)
    click.echo(f"Deleted {store.delete_messages(message_ids)} message(s).")


@mailbox.command("clear")
@click.option("--yes", is_flag=True, help="Clear without prompting.")
@click.pass_obj
def clear_messages(store: MailboxStore, yes: bool):
    """Permanently empty the inbox."""
    if not yes:
        click.confirm("Delete every message in this inbox?", abort=True)
    click.echo(f"Deleted {store.clear_messages()} message(s).")


if __name__ == "__main__":
    mailbox()
