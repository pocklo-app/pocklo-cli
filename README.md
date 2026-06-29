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
disk login --print-url
```

Interactive login:

```bash
disk login
```

You can also paste the full callback URL directly:

```bash
disk login --code 'https://d.6-79.cn/oauth/qtb/callback?code=...'
```

Show the authenticated user:

```bash
disk whoami
```

List the root directory:

```bash
disk ls
```

List a nested remote directory by path:

```bash
disk ls /Photos/2024
```

List a remote resource directly by resource ID:

```bash
disk ls id:Ab12Cd
```

Upload a local file into a remote directory:

```bash
disk upload ./report.pdf /Docs
```

Upload a local directory recursively. The local directory name is created or reused under the target remote directory:

```bash
disk upload ./albums /Photos
```

## State File

The CLI stores the backend JWT token and the last fetched user payload in:

```text
~/.config/disk-cli/state.json
```

Use `--config` to point to a different state file.
