# GreenCraft

A self-updating launcher for a private modded Minecraft server.

Friends run one small installer. It sets up networking, installs
[Prism Launcher](https://prismlauncher.org/), pulls the exact mod set the server is running,
pre-fills the server entry, and launches the game. Every launch afterwards checks this repo for
changes and syncs before starting. When the server's mods are updated, everyone's client follows
automatically, with nothing to explain in chat.

## Install

Download [GreenCraftSetup.exe](../../releases/latest) and run it.

You need Windows, **Minecraft: Java Edition** (not Bedrock) on a Microsoft account with an Xbox
profile, and about 3 GB free. Sam sends you a personal invite link for the network.

The installer reads [`bootstrap.txt`](bootstrap.txt), downloads the launcher, checks it against
the sha512 published there, and starts it. It unpacks nothing into your temp folder and leaves
nothing behind.

## How it works

The server runs two channels. `stable` is what friends play on. `experimental` tests an update
before anyone else sees it. Both are described by a single [`manifest.json`](manifest.json) in this
repo, and each channel's `pack` object is a valid
[`.mrpack`](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack) index,
so it can be imported into any launcher that speaks that format.

Updating is a promotion: whatever has been tested on experimental is copied verbatim into stable
and committed. The commit diff is the release note. It shows exactly which mods clients are about
to receive.

Every file carries a sha512 the client verifies before installing. Nothing is downloaded from
anywhere but Mojang, Modrinth, CurseForge and GitHub.

## Repository layout

| Path | What |
|---|---|
| `manifest.json` | Both channels: mod list, hashes, download URLs, server address |
| `bootstrap.txt` | The payload URL and hash the installer reads. Generated, never edited by hand |
| `overrides/<channel>/` | Config seeded into a fresh install and never overwritten afterwards |
| `updater/` | The launcher, and the installer friends download |
| `tools/` | Manifest authoring, overrides packaging, pre-publish checks, promotion |

The overrides bundles and launcher binaries are attached to [Releases](../../releases) rather than
committed.

## Licence

[MIT](LICENSE). Minecraft itself is not redistributed here. The launcher downloads it from Mojang
at install time. Mods are downloaded from their own publishers and remain under their own licences.
