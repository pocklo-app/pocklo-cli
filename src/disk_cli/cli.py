from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text

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

TYPE_STYLES = {
    RTYPE_FILE: "white",
    RTYPE_FOLDER: "bold cyan",
    RTYPE_LINK: "bold magenta",
}

STATUS_STYLES = {
    0: "bold green",
    1: "bold yellow",
    2: "bold red",
}

console = Console()
error_console = Console(stderr=True)


class CLIUsageError(RuntimeError):
    pass


@dataclass
class CommandContext:
    settings: DiskSettings
    client: DiskClient

    def require_login(self) -> None:
        if not self.settings.token:
            raise CLIUsageError("Please run `htx login` first.")

    def sync_user(self) -> Dict[str, Any]:
        self.require_login()
        user = self.client.get_current_user()
        self.settings.user = user
        if not self.settings.cwd_res_id:
            self.settings.cwd_res_id = user.get("root_res")
            self.settings.cwd_path = "/"
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

    def current_dir_res_id(self) -> str:
        current_id = self.settings.cwd_res_id
        if current_id:
            return current_id
        current_id = self.root_res_id()
        self.settings.cwd_res_id = current_id
        self.settings.cwd_path = "/"
        self.settings.save()
        return current_id

    def current_dir_path(self) -> str:
        return self.settings.cwd_path or "/"

    def resource_path(self, resource: Dict[str, Any]) -> str:
        info = resource["info"]
        root_res_id = self.root_res_id()
        if info["res_str_id"] == root_res_id:
            return "/"

        path_ids = list(reversed(self.client.get_path(info["res_str_id"])))
        if not path_ids or path_ids[0] != root_res_id:
            return "/" + str(info["rname"]).strip("/")

        names: List[str] = []
        for res_str_id in path_ids[1:]:
            if res_str_id == info["res_str_id"]:
                names.append(str(info["rname"]))
                continue
            ancestor = self.client.get_resource(res_str_id)
            names.append(str(ancestor["info"]["rname"]))
        return "/" + "/".join(part.strip("/") for part in names if part)

    def set_current_dir(self, resource: Dict[str, Any]) -> str:
        ensure_folder_resource(resource, label=resource["info"]["rname"])
        self.settings.cwd_res_id = resource["info"]["res_str_id"]
        self.settings.cwd_path = self.resource_path(resource)
        self.settings.save()
        return self.settings.cwd_path

    def reset_current_dir(self) -> str:
        root_resource = self.client.get_resource(self.root_res_id())
        return self.set_current_dir(root_resource)


@dataclass(frozen=True)
class UploadFilters:
    include_exts: Optional[set[str]] = None
    exclude_exts: Optional[set[str]] = None

    def matches(self, local_path: Path) -> bool:
        extension = normalize_extension(local_path.suffix)
        if self.include_exts is not None and extension not in self.include_exts:
            return False
        if self.exclude_exts is not None and extension in self.exclude_exts:
            return False
        return True


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
    parser.add_argument("--version", action="version", version=f"htx-cli {__version__}")

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
    login.add_argument(
        "--browser",
        action="store_true",
        help="Force the browser-based web OAuth flow instead of using `qt app oauth`.",
    )

    whoami = subparsers.add_parser("whoami", help="Show the authenticated Disk user.")
    whoami.add_argument("--json", action="store_true", help="Print the raw API payload.")
    subparsers.add_parser("logout", help="Clear the stored login token.")
    subparsers.add_parser("pwd", help="Print the current remote directory.")

    cd_cmd = subparsers.add_parser("cd", help="Change the current remote directory.")
    cd_cmd.add_argument(
        "remote_path",
        nargs="?",
        default="/",
        help="Remote path, @<res_str_id>, or id:<res_str_id>.",
    )

    ls_cmd = subparsers.add_parser("ls", help="List a remote Disk directory.")
    ls_cmd.add_argument(
        "remote_path",
        nargs="?",
        default=".",
        help="Remote path, @<res_str_id>, or id:<res_str_id>.",
    )
    ls_cmd.add_argument("--json", action="store_true", help="Print the raw API payload.")

    upload = subparsers.add_parser("upload", help="Upload a local file or directory.")
    upload.add_argument("local_path", type=Path, help="Local file or directory to upload.")
    upload.add_argument(
        "remote_path",
        nargs="?",
        default=".",
        help="Target remote directory path, @<res_str_id>, or id:<res_str_id>.",
    )
    upload.add_argument(
        "--include-ext",
        action="append",
        default=[],
        metavar="EXT",
        help="Only upload files with this extension. Repeatable; accepts `jpg` or `.jpg`.",
    )
    upload.add_argument(
        "--exclude-ext",
        action="append",
        default=[],
        metavar="EXT",
        help="Skip files with this extension. Repeatable; accepts `log` or `.log`.",
    )

    mkdir = subparsers.add_parser("mkdir", help="Create a remote directory.")
    mkdir.add_argument("name", help="Directory name to create.")
    mkdir.add_argument(
        "parent",
        nargs="?",
        default=".",
        help="Parent remote directory path, @<res_str_id>, or id:<res_str_id>.",
    )

    rm = subparsers.add_parser("rm", help="Delete a remote file or directory.")
    rm.add_argument("remote_path", help="Remote path, @<res_str_id>, or id:<res_str_id>.")
    rm.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Recursively delete a directory and its contents.",
    )

    mv = subparsers.add_parser("mv", help="Move a remote resource into a different directory.")
    mv.add_argument("source", help="Remote path, @<res_str_id>, or id:<res_str_id>.")
    mv.add_argument("target_dir", help="Target directory path, @<res_str_id>, or id:<res_str_id>.")

    rename = subparsers.add_parser("rename", help="Rename a remote resource.")
    rename.add_argument("remote_path", help="Remote path, @<res_str_id>, or id:<res_str_id>.")
    rename.add_argument("new_name", help="New name for the resource.")

    download = subparsers.add_parser("download", help="Download a remote file, link target, or directory.")
    download.add_argument("remote_path", help="Remote path, @<res_str_id>, or id:<res_str_id>.")
    download.add_argument(
        "local_path",
        nargs="?",
        type=Path,
        help="Optional output file path or existing directory.",
    )

    link = subparsers.add_parser("link", help="Create a remote link resource.")
    link.add_argument("name", help="Link resource name.")
    link.add_argument("url", help="Link target URL.")
    link.add_argument(
        "parent",
        nargs="?",
        default=".",
        help="Parent remote directory path, @<res_str_id>, or id:<res_str_id>.",
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


def normalize_extension(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise CLIUsageError("Extension values cannot be empty.")
    if normalized == ".":
        raise CLIUsageError("Extension values must include at least one character.")
    if not normalized.startswith("."):
        normalized = f".{normalized}"
    return normalized


def build_upload_filters(include_exts: List[str], exclude_exts: List[str]) -> UploadFilters:
    normalized_include = {normalize_extension(ext) for ext in include_exts}
    normalized_exclude = {normalize_extension(ext) for ext in exclude_exts}
    return UploadFilters(
        include_exts=normalized_include or None,
        exclude_exts=normalized_exclude or None,
    )


def format_timestamp(timestamp: Any) -> str:
    if timestamp in (None, ""):
        return "-"
    try:
        return dt.datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "-"


def format_size(size: Any) -> str:
    if size is None:
        return "-"
    try:
        value = float(size)
    except (TypeError, ValueError):
        return "-"
    if value < 0:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    if index == 0:
        return f"{int(value)} {units[index]}"
    return f"{value:.1f} {units[index]}"


def type_text(rtype: int) -> Text:
    return Text(TYPE_LABELS.get(rtype, "unknown"), style=TYPE_STYLES.get(rtype, "white"))


def status_text(status: int) -> Text:
    return Text(
        STATUS_LABELS.get(status, str(status)),
        style=STATUS_STYLES.get(status, "white"),
    )


def resource_name_text(resource: Dict[str, Any]) -> Text:
    text = Text()
    text.append(format_resource_ref(resource["res_str_id"]), style="bold yellow")
    text.append(" ")
    text.append(str(resource["rname"]), style="white")
    return text


def display_size(resource: Dict[str, Any]) -> str:
    if resource.get("rtype") != RTYPE_FILE:
        return "-"
    return format_size(resource.get("rsize"))


def progress_columns() -> List[Any]:
    return [
        SpinnerColumn(style="bold cyan"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        DownloadColumn(binary_units=True),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    ]


def print_success(message: str) -> None:
    console.print(f"[bold green]OK[/bold green] {message}")


def print_note(message: str) -> None:
    console.print(f"[bold cyan]INFO[/bold cyan] {message}")


def print_warning(message: str) -> None:
    error_console.print(f"[bold yellow]WARN[/bold yellow] {message}")


def sort_children(children: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        children,
        key=lambda item: (
            0 if item.get("rtype") == RTYPE_FOLDER else 1,
            item.get("rname", "").lower(),
        ),
    )


def ensure_folder_resource(resource: Dict[str, Any], *, label: str) -> None:
    info = resource.get("info", resource)
    if info.get("rtype") != RTYPE_FOLDER:
        raise CLIUsageError(f"{label} must refer to a remote directory.")


def parse_resource_reference(reference: str) -> Tuple[Optional[str], str]:
    if reference.startswith("@"):
        return reference[1:], "resource id"
    if reference.startswith("id:"):
        return reference[3:], "resource id"
    return None, "path"


def resolve_remote_reference(ctx: CommandContext, reference: str) -> Dict[str, Any]:
    ctx.require_login()
    resource_id, _ = parse_resource_reference(reference)
    if resource_id:
        return ctx.client.get_resource(resource_id)

    reference = reference.strip()
    root_id = ctx.root_res_id()
    current_id = root_id if reference.startswith("/") else ctx.current_dir_res_id()
    normalized = reference.strip("/")
    if reference in {"", ".", "/"} or not normalized:
        return ctx.client.get_resource(current_id)

    current_resource = ctx.client.get_resource(current_id)
    for segment in [part for part in normalized.split("/") if part]:
        if segment == ".":
            continue
        if segment == "..":
            current_res_id = current_resource["info"]["res_str_id"]
            if current_res_id == root_id:
                continue
            parent_id = current_resource["info"].get("parent_str_id")
            if not parent_id:
                continue
            current_resource = ctx.client.get_resource(parent_id)
            continue
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


def find_child_folder(ctx: CommandContext, parent_id: str, folder_name: str) -> Optional[Dict[str, Any]]:
    selector = ctx.client.get_selector(parent_id)
    for child in selector.get("child_list", []):
        if child.get("rname") == folder_name:
            if child.get("rtype") != RTYPE_FOLDER:
                raise CLIUsageError(
                    f"Remote item `{folder_name}` exists under the target path, but it is not a directory."
                )
            return child
    return None


def find_child_resource(ctx: CommandContext, parent_id: str, resource_name: str) -> Optional[Dict[str, Any]]:
    selector = ctx.client.get_selector(parent_id)
    for child in selector.get("child_list", []):
        if child.get("rname") == resource_name:
            return child
    return None


def ensure_remote_folder(ctx: CommandContext, parent_id: str, folder_name: str) -> str:
    existing = find_child_folder(ctx, parent_id, folder_name)
    if existing:
        return existing["res_str_id"]
    created = ctx.client.create_folder(parent_id, folder_name)
    print_success(f"created remote folder {folder_name} ({format_resource_ref(created['res_str_id'])})")
    return created["res_str_id"]


def upload_file_to_remote(ctx: CommandContext, local_path: Path, parent_id: str) -> Dict[str, Any]:
    with console.status(f"[bold cyan]Preparing upload for[/bold cyan] {local_path.name}", spinner="dots"):
        upload_token = ctx.client.get_upload_token(parent_id, local_path.name)

    total = local_path.stat().st_size
    with Progress(*progress_columns(), console=console, transient=True) as progress:
        task_id = progress.add_task(f"Uploading {local_path.name}", total=total)

        def on_progress(transferred: int, callback_total: Optional[int]) -> None:
            progress.update(task_id, completed=transferred, total=callback_total or total)

        def on_phase(phase: str) -> None:
            if phase == "finalizing":
                progress.update(task_id, completed=total, total=total, description=f"Finalizing {local_path.name}")

        uploaded = ctx.client.upload_file(
            upload_token,
            local_path,
            progress_callback=on_progress,
            phase_callback=on_phase,
        )
    print_success(
        f"uploaded {local_path.name} -> {uploaded['rname']} ({format_resource_ref(uploaded['res_str_id'])})"
    )
    return uploaded


def ensure_remote_directory_path(
    ctx: CommandContext,
    remote_folder_map: Dict[Path, str],
    root_remote_id: str,
    relative_parent: Path,
) -> str:
    if relative_parent in remote_folder_map:
        return remote_folder_map[relative_parent]

    parts = relative_parent.parts
    current_relative = Path(".")
    current_remote_id = root_remote_id
    for part in parts:
        current_relative = current_relative / part
        existing = remote_folder_map.get(current_relative)
        if existing is not None:
            current_remote_id = existing
            continue
        current_remote_id = ensure_remote_folder(ctx, current_remote_id, part)
        remote_folder_map[current_relative] = current_remote_id
    return current_remote_id


def upload_directory_to_remote(
    ctx: CommandContext,
    local_dir: Path,
    parent_id: str,
    upload_filters: UploadFilters,
) -> Tuple[int, int]:
    root_remote_id = ensure_remote_folder(ctx, parent_id, local_dir.name)
    remote_folder_map: Dict[Path, str] = {Path("."): root_remote_id}
    uploaded_count = 0
    skipped_count = 0

    for current_root, dirnames, filenames in os.walk(local_dir):
        dirnames.sort()
        filenames.sort()
        current_root_path = Path(current_root)
        relative_root = current_root_path.relative_to(local_dir)

        for filename in filenames:
            local_file = current_root_path / filename
            if not upload_filters.matches(local_file):
                skipped_count += 1
                continue
            remote_parent_id = ensure_remote_directory_path(ctx, remote_folder_map, root_remote_id, relative_root)
            upload_file_to_remote(ctx, local_file, remote_parent_id)
            uploaded_count += 1

    return uploaded_count, skipped_count


def resolve_download_destination(resource: Dict[str, Any], local_path: Optional[Path]) -> Path:
    resource_name = resource["info"]["rname"]
    if local_path is None:
        return Path.cwd() / resource_name

    resolved = local_path.expanduser()
    if resolved.exists() and resolved.is_dir():
        return resolved / resource_name
    return resolved


def download_file_to_local(ctx: CommandContext, resource: Dict[str, Any], destination: Path) -> str:
    with Progress(*progress_columns(), console=console, transient=True) as progress:
        task_id = progress.add_task(f"Downloading {resource['info']['rname']}", total=None)

        def on_progress(transferred: int, total: Optional[int]) -> None:
            progress.update(task_id, completed=transferred, total=total)

        return ctx.client.download_resource(
            resource["info"]["res_str_id"],
            destination,
            progress_callback=on_progress,
        )


def download_directory_to_local(
    ctx: CommandContext,
    resource: Dict[str, Any],
    destination: Path,
) -> Tuple[int, int]:
    destination.mkdir(parents=True, exist_ok=True)
    file_count = 0
    directory_count = 1

    for child in sort_children(resource.get("child_list", [])):
        child_resource = ctx.client.get_resource(child["res_str_id"])
        child_info = child_resource["info"]
        child_destination = destination / child_info["rname"]
        if child_info["rtype"] == RTYPE_FOLDER:
            child_files, child_dirs = download_directory_to_local(ctx, child_resource, child_destination)
            file_count += child_files
            directory_count += child_dirs
            continue

        final_url = download_file_to_local(ctx, child_resource, child_destination)
        file_count += 1
        print_note(
            f"downloaded {child_info['rname']} ({format_resource_ref(child_info['res_str_id'])})"
            f" -> {child_destination}"
        )
        print_note(f"source: {final_url}")

    return file_count, directory_count


def delete_resource_recursive(ctx: CommandContext, resource: Dict[str, Any]) -> None:
    info = resource["info"]
    if info["rtype"] == RTYPE_FOLDER:
        for child in sort_children(resource.get("child_list", [])):
            child_resource = ctx.client.get_resource(child["res_str_id"])
            delete_resource_recursive(ctx, child_resource)
    ctx.client.delete_resource(info["res_str_id"])
    print_success(f"deleted {info['rname']} ({format_resource_ref(info['res_str_id'])})")


def print_resource_listing(resource: Dict[str, Any]) -> None:
    info = resource["info"]
    child_list = sort_children(resource.get("child_list", []))

    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold cyan", justify="right")
    summary.add_column()
    summary.add_row("Name", Text(str(info["rname"]), style="bold white"))
    summary.add_row("Ref", Text(format_resource_ref(info["res_str_id"]), style="bold yellow"))
    summary.add_row("Type", type_text(info["rtype"]))
    summary.add_row("Status", status_text(info["status"]))
    summary.add_row(
        "Owner",
        Text(str(info["owner"]["nickname"]), style="white"),
    )
    summary.add_row("Time", Text(format_timestamp(info.get("create_time")), style="white"))

    if info["rtype"] != RTYPE_FOLDER:
        console.print(Panel(summary, border_style="bright_black", box=box.ROUNDED))
        return

    summary.add_row("Items", Text(str(len(child_list)), style="white"))
    console.print(Panel(summary, border_style="bright_black", box=box.ROUNDED))

    if not child_list:
        console.print("[dim](empty)[/dim]")
        return

    table = Table(
        box=box.SIMPLE_HEAVY,
        expand=True,
        show_edge=False,
        header_style="bold cyan",
        pad_edge=False,
    )
    table.add_column("TYPE", no_wrap=True)
    table.add_column("STATUS", no_wrap=True)
    table.add_column("SIZE", justify="right", no_wrap=True)
    table.add_column("CREATED", no_wrap=True)
    table.add_column("RESOURCE", ratio=1, overflow="ellipsis", no_wrap=True)

    for child in child_list:
        table.add_row(
            type_text(child["rtype"]),
            status_text(child["status"]),
            Text(display_size(child), style="white"),
            Text(format_timestamp(child.get("create_time")), style="white"),
            resource_name_text(child),
        )

    console.print(table)


def print_user_summary(user: Dict[str, Any]) -> None:
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold cyan", justify="right")
    summary.add_column()
    summary.add_row("Nickname", Text(str(user.get("nickname", "-")), style="bold white"))
    summary.add_row("Avatar", Text(str(user.get("avatar", "-")), style="white"))
    root_res = user.get("root_res", "-")
    summary.add_row(
        "Root Ref",
        Text(format_resource_ref(root_res) if root_res != "-" else root_res, style="bold yellow"),
    )
    console.print(Panel(summary, border_style="bright_black", box=box.ROUNDED))


def handle_login(ctx: CommandContext, args: argparse.Namespace) -> int:
    if not args.code and not args.browser:
        qt_command = ctx.settings.resolve_qt_command()
        if qt_command:
            qt_code = request_qt_oauth_code(ctx, qt_command)
            if qt_code:
                args.code = qt_code

    auth_url = ctx.settings.oauth_authorize_url()
    if args.print_url:
        console.print(auth_url)
        return 0

    if not args.code:
        console.print(
            Panel(
                Text(
                    "Open the SSO URL below, authorize the app, then paste the callback URL or code back here.\n\n"
                    f"{auth_url}",
                    style="white",
                ),
                title="SSO Login",
                border_style="cyan",
                box=box.ROUNDED,
            )
        )
        if not args.no_open_browser:
            webbrowser.open(auth_url)
        args.code = input("callback URL or code: ").strip()

    code = extract_code(args.code)
    with console.status("[bold cyan]Exchanging authorization code[/bold cyan]", spinner="dots"):
        oauth_body = ctx.client.exchange_code(code)
        ctx.settings.user = oauth_body.get("user") or ctx.client.get_current_user()
        ctx.settings.cwd_res_id = ctx.settings.user.get("root_res")
        ctx.settings.cwd_path = "/"
    ctx.settings.save()
    user = ctx.settings.user or {}
    print_success(
        f"logged in as {user.get('nickname', 'unknown user')} (root: {format_resource_ref(user.get('root_res', 'n/a'))})"
    )
    return 0


def request_qt_oauth_code(ctx: CommandContext, qt_command: str) -> Optional[str]:
    try:
        result = subprocess.run(
            [
                qt_command,
                "app",
                "oauth",
                "--app-id",
                ctx.settings.sso_app_id,
                "--client-name",
                "htx-cli",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        stdout = exc.stdout.strip() if exc.stdout else ""
        message = stderr or stdout
        if message:
            print_note(f"`qt app oauth` unavailable, falling back to browser login: {message}")
        return None

    code = result.stdout.strip()
    if not code:
        print_note("`qt app oauth` returned an empty authorization code, falling back to browser login.")
        return None

    print_note("received OAuth code from `qt app oauth`; exchanging directly with Disk backend")
    return code


def handle_whoami(ctx: CommandContext, _: argparse.Namespace) -> int:
    with console.status("[bold cyan]Fetching user profile[/bold cyan]", spinner="dots"):
        user = ctx.sync_user()
    if _.json:
        console.print_json(json.dumps(user, ensure_ascii=False, indent=2))
        return 0
    print_user_summary(user)
    return 0


def handle_logout(ctx: CommandContext, _: argparse.Namespace) -> int:
    ctx.settings.clear_auth()
    ctx.settings.save()
    print_success("stored credentials cleared")
    return 0


def handle_pwd(ctx: CommandContext, _: argparse.Namespace) -> int:
    with console.status("[bold cyan]Resolving current directory[/bold cyan]", spinner="dots"):
        resource = resolve_remote_reference(ctx, ".")
        current_path = ctx.set_current_dir(resource)
    console.print(current_path)
    return 0


def handle_cd(ctx: CommandContext, args: argparse.Namespace) -> int:
    with console.status("[bold cyan]Changing remote directory[/bold cyan]", spinner="dots"):
        resource = resolve_remote_reference(ctx, args.remote_path)
        ensure_folder_resource(resource, label=args.remote_path)
        current_path = ctx.set_current_dir(resource)
    print_success(
        f"current directory: {current_path} ({format_resource_ref(resource['info']['res_str_id'])})"
    )
    return 0


def handle_ls(ctx: CommandContext, args: argparse.Namespace) -> int:
    with console.status("[bold cyan]Loading resource listing[/bold cyan]", spinner="dots"):
        resource = resolve_remote_reference(ctx, args.remote_path)
    if args.json:
        console.print_json(json.dumps(resource, ensure_ascii=False, indent=2))
        return 0
    print_resource_listing(resource)
    return 0


def handle_upload(ctx: CommandContext, args: argparse.Namespace) -> int:
    local_path = args.local_path.expanduser().resolve()
    if not local_path.exists():
        raise CLIUsageError(f"Local path does not exist: {local_path}")
    upload_filters = build_upload_filters(args.include_ext, args.exclude_ext)

    with console.status("[bold cyan]Resolving target directory[/bold cyan]", spinner="dots"):
        target_resource = resolve_remote_reference(ctx, args.remote_path)
    ensure_folder_resource(target_resource, label=args.remote_path)
    target_id = target_resource["info"]["res_str_id"]

    if local_path.is_file():
        if not upload_filters.matches(local_path):
            print_note(f"skipped {local_path.name} because it does not match the upload extension filters")
            return 0
        upload_file_to_remote(ctx, local_path, target_id)
        return 0

    uploaded_count, skipped_count = upload_directory_to_remote(ctx, local_path, target_id, upload_filters)
    print_note(f"directory upload summary: {uploaded_count} uploaded, {skipped_count} skipped")
    return 0


def handle_mkdir(ctx: CommandContext, args: argparse.Namespace) -> int:
    with console.status("[bold cyan]Resolving parent directory[/bold cyan]", spinner="dots"):
        target_resource = resolve_remote_reference(ctx, args.parent)
    ensure_folder_resource(target_resource, label=args.parent)
    target_id = target_resource["info"]["res_str_id"]

    existing = find_child_resource(ctx, target_id, args.name)
    if existing is not None:
        raise CLIUsageError(f"`{args.name}` already exists under {args.parent}.")

    with console.status(f"[bold cyan]Creating directory[/bold cyan] {args.name}", spinner="dots"):
        created = ctx.client.create_folder(target_id, args.name)
    print_success(f"created {created['rname']} ({format_resource_ref(created['res_str_id'])})")
    return 0


def handle_rm(ctx: CommandContext, args: argparse.Namespace) -> int:
    with console.status("[bold cyan]Resolving resource[/bold cyan]", spinner="dots"):
        resource = resolve_remote_reference(ctx, args.remote_path)
    info = resource["info"]
    if info["rtype"] == RTYPE_FOLDER and resource.get("child_list") and not args.recursive:
        raise CLIUsageError("Directory is not empty. Re-run with `htx rm --recursive`.")

    if info["rtype"] == RTYPE_FOLDER and args.recursive:
        delete_resource_recursive(ctx, resource)
        return 0

    ctx.client.delete_resource(info["res_str_id"])
    print_success(f"deleted {info['rname']} ({format_resource_ref(info['res_str_id'])})")
    return 0


def handle_mv(ctx: CommandContext, args: argparse.Namespace) -> int:
    with console.status("[bold cyan]Resolving source and target[/bold cyan]", spinner="dots"):
        source = resolve_remote_reference(ctx, args.source)
        target_dir = resolve_remote_reference(ctx, args.target_dir)
    ensure_folder_resource(target_dir, label=args.target_dir)

    source_info = source["info"]
    target_info = target_dir["info"]
    if source_info["res_str_id"] == target_info["res_str_id"]:
        raise CLIUsageError("Source and target directory cannot be the same resource.")

    with console.status("[bold cyan]Moving resource[/bold cyan]", spinner="dots"):
        ctx.client.update_resource(
            source_info["res_str_id"],
            parent_str_id=target_info["res_str_id"],
        )
    print_success(
        f"moved {source_info['rname']} ({format_resource_ref(source_info['res_str_id'])})"
        f" -> {target_info['rname']} ({format_resource_ref(target_info['res_str_id'])})"
    )
    return 0


def handle_rename(ctx: CommandContext, args: argparse.Namespace) -> int:
    with console.status("[bold cyan]Resolving resource[/bold cyan]", spinner="dots"):
        resource = resolve_remote_reference(ctx, args.remote_path)
    info = resource["info"]
    with console.status("[bold cyan]Renaming resource[/bold cyan]", spinner="dots"):
        ctx.client.update_resource(info["res_str_id"], rname=args.new_name)
    print_success(
        f"renamed {info['rname']} ({format_resource_ref(info['res_str_id'])})"
        f" -> {args.new_name}"
    )
    return 0


def handle_download(ctx: CommandContext, args: argparse.Namespace) -> int:
    with console.status("[bold cyan]Resolving remote resource[/bold cyan]", spinner="dots"):
        resource = resolve_remote_reference(ctx, args.remote_path)
    destination = resolve_download_destination(resource, args.local_path)
    if resource["info"]["rtype"] == RTYPE_FOLDER:
        file_count, directory_count = download_directory_to_local(ctx, resource, destination)
        print_success(
            f"downloaded directory {resource['info']['rname']} ({format_resource_ref(resource['info']['res_str_id'])})"
            f" -> {destination}"
        )
        print_note(f"entries: {file_count} files across {directory_count} directories")
        return 0

    final_url = download_file_to_local(ctx, resource, destination)
    print_success(
        f"downloaded {resource['info']['rname']} ({format_resource_ref(resource['info']['res_str_id'])})"
        f" -> {destination}"
    )
    print_note(f"source: {final_url}")
    return 0


def handle_link(ctx: CommandContext, args: argparse.Namespace) -> int:
    with console.status("[bold cyan]Resolving parent directory[/bold cyan]", spinner="dots"):
        target_resource = resolve_remote_reference(ctx, args.parent)
    ensure_folder_resource(target_resource, label=args.parent)
    target_id = target_resource["info"]["res_str_id"]

    existing = find_child_resource(ctx, target_id, args.name)
    if existing is not None:
        raise CLIUsageError(f"`{args.name}` already exists under {args.parent}.")

    with console.status(f"[bold cyan]Creating link[/bold cyan] {args.name}", spinner="dots"):
        created = ctx.client.create_link(target_id, args.name, args.url)
    print_success(
        f"created link {created['rname']} ({format_resource_ref(created['res_str_id'])})"
        f" -> {args.url}"
    )
    return 0


def dispatch(args: argparse.Namespace) -> int:
    settings = DiskSettings.load(args.config)
    client = DiskClient(settings)
    ctx = CommandContext(settings=settings, client=client)

    handlers = {
        "login": handle_login,
        "whoami": handle_whoami,
        "logout": handle_logout,
        "pwd": handle_pwd,
        "cd": handle_cd,
        "ls": handle_ls,
        "upload": handle_upload,
        "mkdir": handle_mkdir,
        "rm": handle_rm,
        "mv": handle_mv,
        "rename": handle_rename,
        "download": handle_download,
        "link": handle_link,
    }
    return handlers[args.command](ctx, args)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return dispatch(args)
    except CLIUsageError as exc:
        error_console.print(f"[bold red]ERROR[/bold red] {exc}")
        return 2
    except DiskAPIError as exc:
        error_console.print(f"[bold red]API[/bold red] {exc}")
        if exc.debug_msg:
            print_warning(f"debug: {exc.debug_msg}")
        return 1
    except KeyboardInterrupt:
        error_console.print("[bold yellow]ABORTED[/bold yellow]")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
