# Pocklo CLI · 小兜

`pocklo` is the command-line client for the Pocklo file workspace. It supports:

- SSO-based login by exchanging an authorization `code` for the backend JWT token
- Listing remote Disk directories
- Switching into a remote working directory with `cd`
- Uploading a local file into a remote Disk directory
- Uploading a local directory recursively while recreating its folder structure remotely
- Resumable multipart uploads with real remote-part progress

## Install

```bash
python3 -m pip install git+https://github.com/pocklo-app/pocklo-cli.git
```

For local development:

```bash
pip install -e .
```

## Commands

Print the SSO authorization URL:

```bash
pocklo login --print-url
```

Interactive login:

```bash
pocklo login
```

You can also paste the full callback URL directly:

```bash
pocklo login --code 'https://pocklo.jyonn.space/oauth/qtb/callback?code=...'
```

Show the authenticated user:

```bash
pocklo whoami
```

Change into a remote directory:

```bash
pocklo cd @Ab12Cd
pocklo cd Projects
pocklo cd ../Archive
```

Print the current remote directory:

```bash
pocklo pwd
```

List the current remote directory:

```bash
pocklo ls
```

List the remote root directory explicitly:

```bash
pocklo ls /
```

List a nested remote directory by relative path:

```bash
pocklo ls Photos/2024
```

List a remote resource directly by resource ID:

```bash
pocklo ls id:Ab12Cd
```

The shorter `@资源ID` form works too:

```bash
pocklo ls @Ab12Cd
```

Upload a local file into a remote directory:

```bash
pocklo upload ./report.pdf
```

Upload a local directory recursively. The local directory name is created or reused under the target remote directory:

```bash
pocklo upload ./albums
```

Only upload selected extensions:

```bash
pocklo upload ./albums /Photos --include-ext jpg --include-ext png
```

Exclude specific extensions:

```bash
pocklo upload ./albums /Photos --exclude-ext tmp --exclude-ext log
```

You can combine both flags. `--exclude-ext` takes precedence when both match the same file.

Create a directory under the remote root:

```bash
pocklo mkdir Projects
pocklo cd Projects
pocklo mkdir 2026
```

Create a directory under a specific remote folder:

```bash
pocklo mkdir 2026 /Photos
pocklo mkdir Archive @Ab12Cd
```

Rename a remote resource:

```bash
pocklo rename /Photos/old-name.jpg new-name.jpg
```

Move a remote resource into another remote directory:

```bash
pocklo mv new-name.jpg ../Archive
```

Delete a remote file:

```bash
pocklo rm new-name.jpg
```

Delete a non-empty remote directory recursively:

```bash
pocklo rm --recursive Archive
```

Download a remote file to the current directory:

```bash
pocklo download report.pdf
```

Download a remote file into a chosen local path:

```bash
pocklo download @Ab12Cd ./downloads/report.pdf
```

Download a remote directory recursively:

```bash
pocklo download @Ab12Cd ./downloads/Archive
```

Create a remote link resource:

```bash
pocklo link Search https://www.google.com
```

## State File

The CLI stores the backend JWT token and the last fetched user payload in:

```text
~/.config/pocklo-cli/state.json
```

On first use, Pocklo imports existing login state and the remote working directory from `~/.config/htx-cli` or `~/.config/disk-cli` when present. Existing upload resume records remain in their configured directory. `DISK_*` environment variables are still read as fallbacks. Use `--config` to point to a different state file.

Multipart upload resume records are stored under:

```text
~/.config/pocklo-cli/upload-records/
```
