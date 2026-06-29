from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import __version__
from .api import DiskAPIError, DiskClient
from .config import DEFAULT_STATE_PATH, DiskSettings

RTYPE_FILE = 0
RTYPE_FOLDER = 1
RTYPE_LINK = 2

STATUS_LABELS = {
    0: "public",
    1: "private",
    2: "protected",
}

TYPE_LABELS = {
    RTYPE_FILE: "file",
    RTYPE_FOLDER: "dir",
    RTYPE_LINK: "link",
}


class CLIUsageError(RuntimeError):
    pass


@dataclass
class CommandContext:
    settings: DiskSettings
    client: DiskClient

    def require_login(self) -> None:
        if not self.settings.token:
            raise CLIUsageError("Please run `htx login` first.")

    def sync_user(self) -> dict[str, Any]:
        self.require_login()
        user = self.client.get_current_user()
        self.settings.user = user
        self.settings.save()
        return user

    def root_res_id(self) -> str:
        user = self.settings.user or self.sync_user()
        root_res = user.get("root_res")
        if not root_res:
            user = self.sync_user()
            root_res = user.get("root_res")
        if not root_res:
            raise CLIUsageError("The current user does not have a root resource folder.")
        return root_res


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="htx",
        description="Command-line client for the Disk backend API.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help=f"State file path (default: {DEFAULT_STATE_PATH})",
    )
    parser.add_argument("--version", action="version", version=f"disk-cli {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser("login", help="Authenticate via the SSO flow.")
    login.add_argument("--code", help="SSO authorization code or callback URL.")
    login.add_argument(
        "--print-url",
        action="store_true",
        help="Only print the SSO authorization URL and exit.",
    )
    login.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Do not try to open the authorization URL in a browser.",
    )

    whoami = subparsers.add_parser("whoami", help="Show the authenticated Disk user.")
    whoami.add_argument("--json", action="store_true", help="Print the raw API payload.")
    subparsers.add_parser("logout", help="Clear the stored login token.")

    ls_cmd = subparsers.add_parser("ls", help="List a remote Disk directory.")
    ls_cmd.add_argument(
        "remote_path",
        nargs="?",
        default="/",
        help="Remote path, @<res_str_id>, or id:<res_str_id>.",
    )
    ls_cmd.add_argument("--json", action="store_true", help="Print the raw API payload.")

    upload = subparsers.add_parser("upload", help="Upload a local file or directory.")
    upload.add_argument("local_path", type=Path, help="Local file or directory to upload.")
    upload.add_argument(
        "remote_path",
        nargs="?",
        default="/",
        help="Target remote directory path, @<res_str_id>, or id:<res_str_id>.",
    )

    return parser


def extract_code(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise CLIUsageError("An authorization code is required.")
    if "code=" not in candidate:
        return candidate

    parsed = urlparse(candidate)
    query = parse_qs(parsed.query)
    code = query.get("code", [None])[0]
    if not code:
        raise CLIUsageError("Could not find a `code` query parameter in the pasted URL.")
    return code


def format_remote_path(remote_path: str) -> str:
    if not remote_path or remote_path == "/":
        return "/"
    return "/" + remote_path.strip("/")


def format_resource_ref(res_str_id: str) -> str:
    return f"@{res_str_id}"


def format_timestamp(timestamp: Any) -> str:
    if timestamp in (None, ""):
        return "-"
    try:
        return dt.datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "-"


def format_size(size: Any) -> str:
    try:
        value = float(size)
    except (TypeError, ValueError):
        return "-"
    if value <= 0:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    if index == 0:
        return f"{int(value)} {units[index]}"
    return f"{value:.1f} {units[index]}"


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def render_row(values: list[str]) -> str:
        return "  ".join(value.ljust(widths[index]) for index, value in enumerate(values))

    lines = [render_row(headers), render_row(["-" * width for width in widths])]
    lines.extend(render_row(row) for row in rows)
    return "\n".join(lines)


def sort_children(children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        children,
        key=lambda item: (
            0 if item.get("rtype") == RTYPE_FOLDER else 1,
            item.get("rname", "").lower(),
        ),
    )


def ensure_folder_resource(resource: dict[str, Any], *, label: str) -> None:
    info = resource.get("info", resource)
    if info.get("rtype") != RTYPE_FOLDER:
        raise CLIUsageError(f"{label} must refer to a remote directory.")


def parse_resource_reference(reference: str) -> tuple[str | None, str]:
    if reference.startswith("@"):
        return reference[1:], "resource id"
    if reference.startswith("id:"):
        return reference[3:], "resource id"
    return None, "path"


def resolve_remote_reference(ctx: CommandContext, reference: str) -> dict[str, Any]:
    ctx.require_login()
    resource_id, _ = parse_resource_reference(reference)
    if resource_id:
        return ctx.client.get_resource(resource_id)

    current_id = ctx.root_res_id()
    normalized = reference.strip("/")
    if not normalized:
        return ctx.client.get_resource(current_id)

    current_resource = ctx.client.get_resource(current_id)
    for segment in [part for part in normalized.split("/") if part]:
        ensure_folder_resource(current_resource, label=format_remote_path(reference))
        selector = ctx.client.get_selector(current_resource["info"]["res_str_id"])
        child = next(
            (item for item in selector.get("child_list", []) if item.get("rname") == segment),
            None,
        )
        if child is None:
            raise CLIUsageError(
                f"Remote path segment `{segment}` does not exist under {format_remote_path(reference)}."
            )
        current_resource = ctx.client.get_resource(child["res_str_id"])
    return current_resource


def find_child_folder(ctx: CommandContext, parent_id: str, folder_name: str) -> dict[str, Any] | None:
    selector = ctx.client.get_selector(parent_id)
    for child in selector.get("child_list", []):
        if child.get("rname") == folder_name:
            if child.get("rtype") != RTYPE_FOLDER:
                raise CLIUsageError(
                    f"Remote item `{folder_name}` exists under the target path, but it is not a directory."
                )
            return child
    return None


def ensure_remote_folder(ctx: CommandContext, parent_id: str, folder_name: str) -> str:
    existing = find_child_folder(ctx, parent_id, folder_name)
    if existing:
        return existing["res_str_id"]
    created = ctx.client.create_folder(parent_id, folder_name)
    print(f"created remote folder: {folder_name} ({created['res_str_id']})")
    return created["res_str_id"]


def upload_file_to_remote(ctx: CommandContext, local_path: Path, parent_id: str) -> dict[str, Any]:
    upload_token = ctx.client.get_upload_token(parent_id, local_path.name)
    uploaded = ctx.client.upload_file(upload_token, local_path)
    print(f"uploaded: {local_path} -> {uploaded['rname']} ({uploaded['res_str_id']})")
    return uploaded


def upload_directory_to_remote(ctx: CommandContext, local_dir: Path, parent_id: str) -> None:
    root_remote_id = ensure_remote_folder(ctx, parent_id, local_dir.name)
    remote_folder_map: dict[Path, str] = {Path("."): root_remote_id}

    for current_root, dirnames, filenames in os.walk(local_dir):
        dirnames.sort()
        filenames.sort()
        current_root_path = Path(current_root)
        relative_root = current_root_path.relative_to(local_dir)
        remote_parent_id = remote_folder_map[relative_root]

        for dirname in dirnames:
            child_relative = relative_root / dirname
            remote_folder_map[child_relative] = ensure_remote_folder(ctx, remote_parent_id, dirname)

        for filename in filenames:
            upload_file_to_remote(ctx, current_root_path / filename, remote_parent_id)


def print_resource_listing(resource: dict[str, Any]) -> None:
    info = resource["info"]
    child_list = sort_children(resource.get("child_list", []))
    print(f"Name    {info['rname']}")
    print(f"Ref     {format_resource_ref(info['res_str_id'])}")
    print(f"Type    {TYPE_LABELS.get(info['rtype'], 'unknown')}")
    print(f"Status  {STATUS_LABELS.get(info['status'], info['status'])}")
    print(f"Owner   {info['owner']['nickname']} ({info['owner']['user_id']})")
    print(f"Time    {format_timestamp(info.get('create_time'))}")

    if info["rtype"] != RTYPE_FOLDER:
        return

    print(f"Items   {len(child_list)}")
    if not child_list:
        print("\n(empty)")
        return

    rows = [
        [
            TYPE_LABELS.get(child["rtype"], "unknown"),
            STATUS_LABELS.get(child["status"], str(child["status"])),
            format_size(child.get("rsize")),
            format_timestamp(child.get("create_time")),
            child["rname"],
            format_resource_ref(child["res_str_id"]),
        ]
        for child in child_list
    ]
    print("")
    print(render_table(["TYPE", "STATUS", "SIZE", "CREATED", "NAME", "REF"], rows))


def print_user_summary(user: dict[str, Any]) -> None:
    print(f"Nickname  {user.get('nickname', '-')}")
    print(f"User ID   {user.get('user_id', '-')}")
    print(f"Avatar    {user.get('avatar', '-')}")
    root_res = user.get("root_res", "-")
    print(f"Root Ref  {format_resource_ref(root_res) if root_res != '-' else root_res}")


def handle_login(ctx: CommandContext, args: argparse.Namespace) -> int:
    auth_url = ctx.settings.oauth_authorize_url()
    if args.print_url:
        print(auth_url)
        return 0

    if not args.code:
        print("Open the SSO URL below, authorize the app, then paste the callback URL or code here:\n")
        print(auth_url)
        print("")
        if not args.no_open_browser:
            webbrowser.open(auth_url)
        args.code = input("callback URL or code: ").strip()

    code = extract_code(args.code)
    oauth_body = ctx.client.exchange_code(code)
    ctx.settings.user = oauth_body.get("user") or ctx.client.get_current_user()
    ctx.settings.save()
    user = ctx.settings.user or {}
    print(f"logged in as {user.get('nickname', 'unknown user')} (root: {user.get('root_res', 'n/a')})")
    return 0


def handle_whoami(ctx: CommandContext, _: argparse.Namespace) -> int:
    user = ctx.sync_user()
    if _.json:
        print(json.dumps(user, ensure_ascii=False, indent=2))
        return 0
    print_user_summary(user)
    return 0


def handle_logout(ctx: CommandContext, _: argparse.Namespace) -> int:
    ctx.settings.clear_auth()
    ctx.settings.save()
    print("stored credentials cleared")
    return 0


def handle_ls(ctx: CommandContext, args: argparse.Namespace) -> int:
    resource = resolve_remote_reference(ctx, args.remote_path)
    if args.json:
        print(json.dumps(resource, ensure_ascii=False, indent=2))
        return 0
    print_resource_listing(resource)
    return 0


def handle_upload(ctx: CommandContext, args: argparse.Namespace) -> int:
    local_path = args.local_path.expanduser().resolve()
    if not local_path.exists():
        raise CLIUsageError(f"Local path does not exist: {local_path}")

    target_resource = resolve_remote_reference(ctx, args.remote_path)
    ensure_folder_resource(target_resource, label=args.remote_path)
    target_id = target_resource["info"]["res_str_id"]

    if local_path.is_file():
        upload_file_to_remote(ctx, local_path, target_id)
        return 0

    upload_directory_to_remote(ctx, local_path, target_id)
    return 0


def dispatch(args: argparse.Namespace) -> int:
    settings = DiskSettings.load(args.config)
    client = DiskClient(settings)
    ctx = CommandContext(settings=settings, client=client)

    handlers = {
        "login": handle_login,
        "whoami": handle_whoami,
        "logout": handle_logout,
        "ls": handle_ls,
        "upload": handle_upload,
    }
    return handlers[args.command](ctx, args)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return dispatch(args)
    except CLIUsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except DiskAPIError as exc:
        print(f"api error: {exc}", file=sys.stderr)
        if exc.debug_msg:
            print(f"debug: {exc.debug_msg}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
