# Deployment

How to install, configure, contain and operate the OpenSCAD MCP server. For
what the tools do, see [README.md](README.md) and [API.md](API.md).

The distribution is named `openscad-mcp` and runs OpenSCAD as a subprocess, so
OpenSCAD has to be installed too.

> The PyPI distribution is `openscad-mcp`, first published as 0.6.1. Do not
> install `openscad-mcp-server`: that name belongs to an unrelated project.

## Prerequisites

- Python 3.10 or newer (uv fetches one if you have none).
- OpenSCAD 2021.01 or newer. Development snapshots are supported and
  preferred: they carry the Manifold backend and render PNG headlessly
  through EGL, while 2021.01 needs an X display for PNG export. On 2021.01
  you therefore need a display or `xvfb` to render; mesh export, `measure`
  and `check` work without one.

### Installing OpenSCAD

| Platform | Stable | Development snapshot |
|---|---|---|
| Debian / Ubuntu | `sudo apt install openscad` | `sudo add-apt-repository ppa:openscad/releases && sudo apt install openscad-nightly` |
| Fedora | `sudo dnf install openscad` | [copr / AppImage](https://openscad.org/downloads.html) |
| Arch | `sudo pacman -S openscad` | `openscad-git` (AUR) |
| macOS | `brew install --cask openscad` | `brew install --cask openscad@snapshot` |
| Windows | [installer](https://openscad.org/downloads.html) | "Development snapshot" installer |
| Any Linux | `flatpak install flathub org.openscad.OpenSCAD` | AppImage from the downloads page |

Verify with `openscad --version`. The server probes `openscad-nightly`,
`openscad`, `OpenSCAD` and `openscad.exe` on PATH plus the usual install
locations, runs each one, and keeps the newest version it finds; set
`OPENSCAD_PATH` to override that, and call `check_openscad` to see what it
picked.

### BOSL2

BOSL2 is the library most models include, and the anchor and joint reference
tools assume it. It is found in the standard library directories and anywhere
on `OPENSCADPATH`:

```bash
git clone --depth 1 https://github.com/BelfrySCAD/BOSL2.git \
  ~/.local/share/OpenSCAD/libraries/BOSL2          # Linux
git clone --depth 1 https://github.com/BelfrySCAD/BOSL2.git \
  ~/Documents/OpenSCAD/libraries/BOSL2             # macOS, Windows
```

`get_libraries` lists what the server can see.

## Installing the server

From a checkout, which is what the maintainers run:

```bash
git clone https://github.com/robertcoop/openscad-mcp
cd openscad-mcp
uv sync --extra dev
uv run openscad-mcp
uv run openscad-mcp check examples/checks/turntable.yaml --allow .
```

Without a clone, straight from the repository:

```bash
uvx --from git+https://github.com/robertcoop/openscad-mcp openscad-mcp
uv tool install git+https://github.com/robertcoop/openscad-mcp
```

The package accepts `fastmcp>=2.14.5,<5`. A checkout uses the 2.14.5 pinned
in `uv.lock`; a fresh install resolves 4.x. Both are exercised by CI and the
server, the `check` CLI and the reference tool work on either.

## Client configuration

**Claude Code:**

```bash
claude mcp add openscad --transport stdio -- uvx openscad-mcp
claude mcp add openscad --scope project \
  --env MCP_ALLOWED_PATHS=/home/me/projects \
  --transport stdio -- uvx openscad-mcp
```

To run a checkout instead, replace `uvx openscad-mcp` with
`uv run --directory /home/me/src/openscad-mcp openscad-mcp`.

`--scope local` (default) is you in this project, `--scope project` writes
`.mcp.json` for the team, `--scope user` applies everywhere.

The repository is also a Claude Code plugin, which brings the server and the
`openscad-design` skill together:

```
/plugin marketplace add robertcoop/openscad-mcp
/plugin install openscad-mcp@openscad-mcp
```

**Claude Desktop** reads `~/Library/Application Support/Claude/claude_desktop_config.json`
on macOS and `%APPDATA%\Claude\claude_desktop_config.json` on Windows.
**Cursor, Windsurf and VS Code** read a project `.mcp.json`. Both take the
same shape. Any setting from the table below goes in `env`:

```json
{
  "mcpServers": {
    "openscad": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/home/me/src/openscad-mcp", "openscad-mcp"],
      "env": {
        "OPENSCAD_PATH": "/usr/bin/openscad-nightly",
        "MCP_ALLOWED_PATHS": "/home/me/projects/enclosure:/home/me/projects/shared-libs",
        "MCP_MAX_MEMORY_MB": "4096",
        "MCP_RENDER_TIMEOUT": "300",
        "MCP_LOG_LEVEL": "INFO"
      }
    }
  }
}
```

That runs a checkout. For the published package, replace the command and args
with `"command": "uvx"` and `"args": ["openscad-mcp@0.6.1"]` and leave `env`
alone. `MCP_ALLOWED_PATHS` is separated by `:` on POSIX and
`;` on Windows, and must include the library directories, not just the
project. Restart the client after editing; it holds the server open.

## Configuration

Every setting has an environment variable and a YAML key.
[.env.example](.env.example) is the annotated list of variables with their
defaults; a `.env` file in the working directory is loaded automatically. The
settings that matter most in a deployment:

| Variable | Default | Effect |
|---|---|---|
| `OPENSCAD_PATH` | auto-detected | Binary to run |
| `MCP_ALLOWED_PATHS` | unset | Directories scripts may read, `:`-separated. Unset means no validation |
| `MCP_MAX_MEMORY_MB` | 4096 | `RLIMIT_AS` per OpenSCAD process on POSIX; 0 disables |
| `MCP_RENDER_TIMEOUT` | 300 | Seconds before a subprocess is killed |
| `MCP_MAX_CONCURRENT_RENDERS` | 5 | Simultaneous OpenSCAD processes |
| `MCP_MAX_IMAGE_WIDTH` / `_HEIGHT` | 1568 | Requested sizes are clamped, aspect preserved |
| `MCP_MAX_FILE_SIZE_MB` | 10 | Largest inline `scad_content` accepted |
| `MCP_HARD_WARNINGS` | false | Restores `--hardwarnings`; see the README for why it is off |
| `MCP_CACHE_ENABLED` / `_SIZE_MB` / `_TTL_HOURS` | true / 500 / 24 | Render cache |
| `MCP_TEMP_DIR` | `/tmp/openscad-mcp` | Scratch directory |
| `MCP_TRANSPORT` / `MCP_HOST` / `MCP_PORT` | stdio / localhost / 8000 | Transport |
| `MCP_LOG_LEVEL` / `MCP_LOG_FILE` | INFO / none | Logging; the file handler rotates |

The same keys nest in YAML under `server`, `rendering`, `cache`, `security`
and `logging`, loaded with `Config.from_yaml`. YAML is the only way to move
the cache directory; there is no environment variable for it.

## Running as a service

stdio is the default transport and needs no service: the client starts the
process and owns its lifetime. A long-lived service only makes sense for the
`http` transport, which serves MCP at `http://HOST:PORT/mcp`.

```ini
# /etc/systemd/system/openscad-mcp.service
[Unit]
Description=OpenSCAD MCP Server
After=network.target

[Service]
Type=simple
User=openscad-mcp
Group=openscad-mcp
WorkingDirectory=/var/lib/openscad-mcp
Environment=MCP_TRANSPORT=http
Environment=MCP_HOST=127.0.0.1
Environment=MCP_PORT=8000
Environment=MCP_ALLOWED_PATHS=/var/lib/openscad-mcp/projects
Environment=MCP_MAX_CONCURRENT_RENDERS=4
Environment=MCP_TEMP_DIR=/var/lib/openscad-mcp/tmp
Environment=HOME=/var/lib/openscad-mcp
ExecStart=/opt/openscad-mcp/bin/openscad-mcp
Restart=on-failure
RestartSec=10

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/openscad-mcp

[Install]
WantedBy=multi-user.target
```

Install into a virtualenv the service user owns:

```bash
sudo python3 -m venv /opt/openscad-mcp
sudo /opt/openscad-mcp/bin/pip install openscad-mcp
sudo systemctl daemon-reload && sudo systemctl enable --now openscad-mcp
journalctl -u openscad-mcp -f
```

`HOME` matters: the render cache is written to `$HOME/.cache/openscad-mcp`.
On OpenSCAD 2021.01 add `Environment=DISPLAY=:99` and a companion Xvfb unit,
or renders will fail while everything else keeps working. Bind to 127.0.0.1
and put a TLS-terminating reverse proxy in front if the port has to leave the
host; the server itself does no authentication.

## Docker

[Dockerfile](Dockerfile) builds a Debian image with OpenSCAD 2021.01, Xvfb and
BOSL2. The default target installs the checkout; `release` installs a
published version from PyPI.

```bash
docker build -t openscad-mcp .
docker build --target release --build-arg OPENSCAD_MCP_VERSION=0.6.1 -t openscad-mcp:rel .

docker run --rm -i -v "$PWD:/work" openscad-mcp                 # stdio server
docker run --rm -v "$PWD:/work" openscad-mcp check checks.yaml  # one check run
```

The entrypoint starts Xvfb and then `exec`s the server, so the server is PID 1
and the exit code survives. `xvfb-run` is deliberately not used: as PID 1 it
never reaps its child and the container hangs after the command finishes.
`MCP_ALLOWED_PATHS=/work` is set in the image, so the mounted project is the
only thing a script can read.

Mount a volume at `/home/mcp/.cache/openscad-mcp` to keep the cache between
runs. To use the image as the MCP server, set the client's `command` to
`docker` and its `args` to
`["run", "--rm", "-i", "-v", "/home/me/projects:/work", "openscad-mcp"]`.

## Security hardening

The threat model is in the README and is worth reading before exposing this to
anything untrusted. In deployment terms:

- **Set `MCP_ALLOWED_PATHS`.** Unset, no path validation happens at all and
  the server logs a warning saying so. Set, both the arguments (`scad_file`,
  `include_paths`, export `output_path`) and the whole dependency closure
  OpenSCAD actually read are checked, and output is withheld if anything falls
  outside. That closes `include <...>` and `surface(file=...)` as ways to read
  arbitrary files.
- **Keep the memory ceiling.** `MCP_MAX_MEMORY_MB` applies `RLIMIT_AS` per
  OpenSCAD process on POSIX hosts. OpenSCAD has none of its own, and a small
  `minkowski()` can consume the whole machine. `MCP_RENDER_TIMEOUT` bounds
  every subprocess.
- Echo output is capped at 200 lines of 2000 characters and labelled as
  untrusted content from the rendered file.
- There is **no OS-level sandbox**: no network isolation, no filesystem
  namespace. For untrusted input use the container above, or Landlock or
  bubblewrap with only the project directory visible.
- Run as a dedicated unprivileged user, and give the HTTP transport a reverse
  proxy rather than a public port.

## Cache and disk use

Everything is cached under `~/.cache/openscad-mcp`: rendered PNGs with their
dependency manifests at the top level, per-part meshes and CSG dumps in
`parts/`. Keys cover everything that went into the call, so an ordinary edit
invalidates the entry.

`MCP_CACHE_SIZE_MB` (500) and `MCP_CACHE_TTL_HOURS` (24) bound the render
entries only; eviction deletes the oldest files by modification time until the
total is back under the cap. The `parts/` meshes are neither counted nor
evicted, so watch the directory on a busy host:

```bash
du -sh ~/.cache/openscad-mcp ~/.cache/openscad-mcp/parts
```

The `clear_cache` tool removes both. Deleting the directory by hand is safe;
it is rebuilt on the next call. `MCP_TEMP_DIR` holds scratch files for inline
models and should be on fast local disk.

## Upgrading

```bash
git -C /home/me/src/openscad-mcp pull && uv sync --extra dev   # a checkout
uv tool upgrade openscad-mcp                                   # uv tool install
uvx openscad-mcp@latest                                        # newest release
pip install --upgrade openscad-mcp
```

`uvx` caches by version, so a client configured as `uvx openscad-mcp` keeps
running the cached build until you upgrade or ask for `@latest`; a Git install
caches by commit, and `uvx --refresh` re-resolves it. Restart the client
afterwards. To roll back, pin the previous version and restart.

## Checks in CI

`openscad-mcp check` runs a check file without a client and exits 0 when
everything passes, 1 on any FAIL, 2 when only unresolved rows remain, and 3 on
an error. [examples/checks/turntable.yaml](examples/checks/turntable.yaml) is
an annotated file exercising every rule.

```make
# Makefile
CHECKS  := $(wildcard checks/*.yaml)
OSCHECK := uvx openscad-mcp@0.6.1

.PHONY: check
check:
	@for f in $(CHECKS); do \
	    echo "== $$f"; \
	    $(OSCHECK) check $$f --allow $(CURDIR) || exit $$?; \
	done

.PHONY: check-json
check-json:
	@$(OSCHECK) check checks/assembly.yaml --allow $(CURDIR) --json > check-report.json
```

`--model FILE` points the same rules at another `.scad`, `--fn N` overrides
tessellation, `--allow DIR` is repeatable. In a GitHub Actions job, install
OpenSCAD with `sudo apt-get install -y openscad xvfb` and run `make check`;
mesh-based rules need no display.

## Troubleshooting

**"OpenSCAD not found" at startup.** The server logs this and keeps running,
because the read-only tools still work. Run `which openscad` and set
`OPENSCAD_PATH` to the absolute path. On macOS it is inside the bundle:
`/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD`.

**Renders fail but `measure` and `check` work.** That is the display problem
on 2021.01. Run under `xvfb-run -a openscad-mcp`, set `DISPLAY` to a real
one, or install a development snapshot, which renders through EGL.

**Blank images or truncated echo output.** `--hardwarnings` stops evaluation
at the first warning while still exiting 0. It is off by default; if you set
`MCP_HARD_WARNINGS=true`, this is why.

**"outside allowed paths".** The file, one of `include_paths`, or something
the model read through `include`/`use`/`import`/`surface` is not under
`MCP_ALLOWED_PATHS`. Containment uses resolved paths, so a symlink into the
directory does not count. Add the real directory, libraries included.

**Stale results after editing a file.** The cache key covers the model and
its dependency manifest, so ordinary edits invalidate it. If something is
genuinely stuck, call `clear_cache` or delete `~/.cache/openscad-mcp`.

**Client shows no tools.** Run the exact command from the config in a
terminal. It must print nothing on stdout: on the stdio transport stdout is
the JSON-RPC channel, and a stray print breaks the handshake. Set
`MCP_LOG_LEVEL=DEBUG` and `MCP_LOG_FILE` to capture the startup logs.
