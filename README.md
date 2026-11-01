<h1 align="center">Peppermint</h1>

<p align="center">One-command PocketMine-MP.phar builder for pmmp, NetherGames and other forks.</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.9%2B-DC1E30?style=for-the-badge&labelColor=FFFFFF" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/PocketMine--MP-5.x-DC1E30?style=for-the-badge&labelColor=FFFFFF" alt="PocketMine-MP 5.x">
  <img src="https://img.shields.io/badge/dependencies-none-DC1E30?style=for-the-badge&labelColor=FFFFFF" alt="No dependencies">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-DC1E30?style=for-the-badge&labelColor=FFFFFF" alt="Windows, Linux, macOS">
</p>

---

**Peppermint** is a terminal tool that turns a PocketMine-MP Git repository into a ready-to-run `PocketMine-MP.phar`. Paste a repository link, choose a branch, and it clones, installs and compiles for you.

It was made for the [NetherGames fork](https://github.com/NetherGamesMC/PocketMine-MP) and also works with [pmmp/PocketMine-MP](https://github.com/pmmp/PocketMine-MP) and other forks that use the standard `build/server-phar.php` script.

## Features

- **One link in, one phar out.** It runs clone, `composer install` and the phar build in one go.
- **Flexible links.** It accepts `https://` URLs, `owner/repo`, `.../tree/<branch>` links, SSH URLs and local paths.
- **Branch lookup.** It detects the repository's default branch and suggests it.
- **No setup.** It uses only the Python standard library, finds your PocketMine PHP binary, and downloads and checksum-verifies Composer by itself.
- **Missing PHP extensions don't stop the build.** Extensions are only needed to *run* the server, so the build skips any your PHP lacks and lists them at the end.
- **Verified output.** It opens the finished phar with PHP to make sure it isn't broken, then shows the version, commit and size.
- **Safe.** It never overwrites a phar without first making a timestamped backup.
- **Server install.** It can copy the result straight into your server folder.
- **Logs.** Every build writes a full log to `logs/`.

## Requirements

| Tool | Version | Notes |
|---|---|---|
| Python | 3.9+ | Standard library only |
| Git | any recent | Must be on `PATH` |
| PHP | 8.1+ | A **PocketMine-MP PHP binary** (with `pmmpthread`, `chunkutils2`, `leveldb`, …) |
| Composer | — | Downloaded automatically if missing |

> Use the same PHP binary your server runs on, i.e. the one in your server's `bin/php` folder. A regular PHP install lacks the PocketMine extensions and is rejected.

## Installation

```bash
git clone https://github.com/YOUR-USERNAME/peppermint.git
cd peppermint
```

There is nothing else to install.

## Usage

### Interactive

```bash
python peppermint.py
```

On Windows you can also double-click **`Peppermint.cmd`**.

You will be asked for:

| Prompt | Default |
|---|---|
| Repository | `https://github.com/NetherGamesMC/PocketMine-MP` |
| Branch | The repository's default branch |
| Output | `builds/PocketMine-MP.phar` |

### Non-interactive

```bash
python peppermint.py --yes --repo NetherGamesMC/PocketMine-MP --branch stable --out ./PocketMine-MP.phar
```

### Options

| Option | Description |
|---|---|
| `--repo <link>` | Repository link, `owner/repo` or local path |
| `--branch <name>` | Branch or tag to build |
| `--out <path>` | Output `.phar` path |
| `--php <path>` | PHP binary to use |
| `--yes`, `-y` | Use defaults and arguments; ask nothing |
| `--no-anim` | Skip the intro animation |
| `--update-composer` | Download `composer.phar` again |
| `--keep-source` | Keep the cloned source in `.work/` after the build |

### Environment variables

| Variable | Purpose |
|---|---|
| `PHP_BINARY` | PHP binary to use when `--php` is not given |
| `GITHUB_TOKEN` | Passed to Composer to avoid GitHub download rate limits |

## Finding PHP

When `--php` is not set, these locations are tried in order:

1. `PHP_BINARY` environment variable
2. `./bin/php/` next to the tool
3. `../server/bin/php/` (the tool placed next to a server folder)
4. `../bin/php/`
5. `php` on `PATH`

## How it works

```
git clone --depth 1 --branch <branch> <repo>
php composer.phar install --no-dev --classmap-authoritative --prefer-dist
php -dphar.readonly=0 build/server-phar.php --out <output>
```

The cloned source is deleted when the build finishes, unless you pass `--keep-source`.

## Project layout

```
peppermint/
├── peppermint.py       # the application
├── Peppermint.cmd      # Windows launcher
├── builds/             # output (git-ignored)
├── logs/               # build logs (git-ignored)
├── .cache/             # composer.phar (git-ignored)
└── .work/              # temporary clones (git-ignored)
```

## Troubleshooting

| Message | Fix |
|---|---|
| `PHP not found` | Pass `--php <path>` or set `PHP_BINARY`. |
| `PHP is missing: …` | You are using a regular PHP install. Use the PocketMine PHP binary. |
| `Repository not reachable` | Check the link, branch and network connection. |
| `▲ Missing PHP extension: webrtc` | Not an error. The build succeeded; the server runs without NetherNet support on that PHP. |
| `File in use` | Stop the server before copying the phar into it. |
| Composer rate limit | Set `GITHUB_TOKEN` to a GitHub personal access token. |

The complete output of every step is saved in `logs/`.

## Disclaimer

This project is not affiliated with PocketMine-MP or NetherGames. It builds source code you choose; the licences of those projects apply to the resulting phar.
