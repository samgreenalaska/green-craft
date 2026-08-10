# GreenCraft

A self-updating launcher for a private modded Minecraft server.

Friends run one small installer. It sets up networking, installs
[Prism Launcher](https://prismlauncher.org/), pulls the exact mod set the server is running,
pre-fills the server entry, and launches the game. Every launch afterwards checks this repo for
changes and syncs before starting — so when the server's mods are updated, everyone's client
follows automatically with nothing to explain in chat.

> **Status: in development.** The manifest, overrides bundle and release tooling work. The
> launcher and installer themselves are not written yet, so there is nothing here to download and
> run. See [Current state](#current-state).

## How it works

The server runs two channels — `stable`, which friends play on, and `experimental`, used to test
an update before anyone else sees it. Both are described by a single [`manifest.json`](manifest.json)
in this repo, and each channel's `pack` object is a valid
[`.mrpack`](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack) index,
so it can be imported into any launcher that speaks that format.

Updating is a promotion: whatever has been tested on experimental is copied verbatim into stable
and committed. The commit diff is the release note — it shows exactly which mods clients are about
to receive.

Every file carries a sha512 the client verifies before installing. Nothing is downloaded from
anywhere but Mojang, Modrinth, CurseForge and GitHub.

## Repository layout

| Path | What |
|---|---|
| `manifest.json` | Both channels: mod list, hashes, download URLs, server address |
| `overrides/<channel>/` | Config seeded into a fresh install and never overwritten afterwards |
| `tools/` | Manifest authoring, overrides packaging, pre-publish checks, promotion |

Release assets — the overrides bundles and, later, the launcher binaries — are attached to
[Releases](../../releases) rather than committed.

## Current state

Working:

- `manifest.json` describing both channels, with every download URL verified reachable
- overrides bundle, built reproducibly (identical input always yields an identical hash)
- tooling to rescan a local instance, rebuild the manifest, promote, and publish

Not built yet:

- `GreenCraft.exe` — the updater/launcher
- `GreenCraftSetup.exe` — the installer friends actually download

## For friends

Nothing to do yet. When there is something to install, you will get a download link and a personal
invite link. You will need **Minecraft: Java Edition** (not Bedrock) on a Microsoft account with an
Xbox profile, Windows, and about 3 GB free.

## Licence

[MIT](LICENSE). Minecraft itself is not redistributed here — the launcher downloads it from Mojang
at install time. Mods are downloaded from their own publishers and remain under their own licences.
