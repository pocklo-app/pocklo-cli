# disk-cli

`disk-cli` is a command-line client for the Disk backend API. It supports:

- SSO-based login by exchanging an authorization `code` for the backend JWT token
- Listing remote Disk directories
- Uploading a local file into a remote Disk directory
- Uploading a local directory recursively while recreating its folder structure remotely

## Install

```bash
source /Users/jyonn/Projects/venv/django/bin/activate
pip install -e .
```

## Commands

Print the SSO authorization URL:

```bash
htx login --print-url
```

Interactive login:

```bash
htx login
```

You can also paste the full callback URL directly:

```bash
htx login --code 'https://d.6-79.cn/oauth/qtb/callback?code=...'
```

Show the authenticated user:

```bash
htx whoami
```

List the root directory:

```bash
htx ls
```

List a nested remote directory by path:

```bash
htx ls /Photos/2024
```

List a remote resource directly by resource ID:

```bash
htx ls id:Ab12Cd
```

The shorter `@资源ID` form works too:

```bash
htx ls @Ab12Cd
```

Upload a local file into a remote directory:

```bash
htx upload ./report.pdf /Docs
```

Upload a local directory recursively. The local directory name is created or reused under the target remote directory:

```bash
htx upload ./albums /Photos
```

Create a directory under the remote root:

```bash
htx mkdir Projects
```

Create a directory under a specific remote folder:

```bash
htx mkdir 2026 /Photos
htx mkdir Archive @Ab12Cd
```

Rename a remote resource:

```bash
htx rename /Photos/old-name.jpg new-name.jpg
```

Move a remote resource into another remote directory:

```bash
htx mv /Photos/new-name.jpg /Archive
```

Delete a remote file:

```bash
htx rm /Archive/new-name.jpg
```

Delete a non-empty remote directory recursively:

```bash
htx rm --recursive /Archive
```

Download a remote file to the current directory:

```bash
htx download /Docs/report.pdf
```

Download a remote file into a chosen local path:

```bash
htx download @Ab12Cd ./downloads/report.pdf
```

Create a remote link resource:

```bash
htx link Search https://www.google.com /Bookmarks
```

## State File

The CLI stores the backend JWT token and the last fetched user payload in:

```text
~/.config/disk-cli/state.json
```

Use `--config` to point to a different state file.
