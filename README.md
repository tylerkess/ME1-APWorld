# Archipelago for Mass Effect 1 (Legendary Edition)

An [Archipelago](https://archipelago.gg) multiworld randomizer for Mass Effect 1 in Mass Effect Legendary Edition.

**Status: beta test (0.2.12).** It's playable from start to finish on the Steam release (English). Expect bugs.

## What changes

Shepard starts as a Spectre on the Normandy with no squad. Eden Prime, Therum, Feros, Noveria, Virmire and Asteroid X57
stay locked on the galaxy map until you find their coordinates. Squadmates, weapons, armor, bio-amps, omni-tools and
upgrades are all items. Ilos opens once Virmire is done and you've collected enough Prothean Beacon Fragments. You win
by defeating Saren and Sovereign on the Citadel.

Checks include story milestones, recruitments, Codex pages, journal entries, shop slots, and (optionally) containers and Aid Stations. Sent and received
items show as notices on the HUD, infinite storm can be enabled, and the galaxy-map travel videos are skipped.

**In-game tracker:** the client works out your seed's logic as you play. Press **F7** for a summary box (checks in logic
per region, every uncharted planet worth visiting, what each locked world still needs), check the **Archipelago** assignment in the journal (fragments,
coordinates, squad, checks in logic), and look for "Archipelago" callouts on the galaxy map. See the [game page](worlds/mass_effect_le1/docs/en_Mass%20Effect%20Legendary%20Edition%201.md) for the details.

## Download and install

**[Setup guide](worlds/mass_effect_le1/docs/setup_en.md)**: the full install steps.

**Recommended: the setup app.** From the [releases page](../../releases) (the newest is at the top), download
`MassEffectAPSetup-<version>-ME1.zip`, unzip it and run **Mass Effect AP Setup.exe**. It installs the world, the mod
and the bridge (no Mod Manager needed, except once for the ASI loader and LE1 Autoload Enabler from its ASI Manager),
writes your YAML, can run Generate, and launches the game and client. Uninstall puts every file it changed back.

**Manual install:** download `mass_effect_le1.apworld`, `ArchipelagoME1-mod-<version>.zip`,
`ArchipelagoME1-bridge-<version>.zip` and (optional) `Mass Effect Legendary Edition 1.yaml`, then follow the
[setup guide](worlds/mass_effect_le1/docs/setup_en.md): double-click the apworld (Archipelago **0.6.8 or newer**), import
and apply the mod zip in ME3Tweaks Mod Manager and install the ASI loader, and run `install-bridge.ps1` from the
unzipped bridge. Start a **new career**, then open **Mass Effect LE1 Client** from the Archipelago Launcher and connect
before you open any shop. A career from before 0.2.4: type `/bind` in the client once.

Mass Effect 1 is a custom world: make your YAML in the Launcher's **Options Creator**, generate with **Generate** in
the Launcher, then host the seed on [archipelago.gg](https://archipelago.gg/uploads) (upload the generated zip) or with
**Host** in the Launcher.

The YAML template (`Mass Effect Legendary Edition 1.yaml`) is on the release page.

## Reporting bugs

Open an [issue](../../issues) and include:

- What you did and what happened (the planet or mission, what you picked up or talked to).
- The newest `ME1Client_*.txt` from Archipelago's `logs` folder (for example `C:\ProgramData\Archipelago\logs`).
- Your YAML and the seed, if the problem is about logic (something unreachable, or reachable too early).

## Repository layout

- `worlds/mass_effect_le1/`: the Archipelago world (logic, items, locations, options) and the client.
- `mod/`: the game mod (`DLC_MOD_ArchipelagoME1`, and `BASEGAME` for the game packages it replaces); `tools/le1mod/`
  applies this repo's patches to it.
- `tools/le1apbridge/`: the bridge (`LE1APBridge.asi`, built) and its checks table.
- `tools/le1apextras/`: LE1APExtras, a small ASI next to the bridge (squad level, Experience items): source, build
  script and the built `.asi`.
- `tools/release/`: the release packaging script and the bridge installer.
- `tools/setup/`: the source of Mass Effect AP Setup, the setup app.
- `vendor/`: Archipelago, LegendaryExplorer and LExASIs (with LExSDKv2, for LE1APExtras) as pinned submodules.

## Development

Everything comes from this repository. Clone it with its submodules:

```
git clone --recurse-submodules <this repo>
```

Set up Archipelago's environment once (Python 3.11 or newer): `python -m venv vendor/Archipelago/.venv`, then install
`vendor/Archipelago/requirements.txt` and `pytest` into it.

- Tests: `python tools/test_world.py`.
- Game mod patches: `python tools/le1mod/build_mod.py <LE1 game folder>` (see `tools/le1mod/README.md`).
- Setup app: `python tools/setup/package.py <app version>`, then `python tools/setup/test_setup.py --exe <its exe>` (see
  `tools/setup/README.md`; in WSL use `python3`).
- LE1APExtras: `python tools/le1apextras/build.py` (see `tools/le1apextras/README.md`).

### Making a release

Releases are published on the fork (the `fork` remote), from the release branch. In WSL use `python3`.

1. Bump the versions and commit: `world_version` in `worlds/mass_effect_le1/archipelago.json` and
   `tools/setup/options-me1.json`, the status line at the top of this README, and, when the mod changed,
   `MOD_VERSION` in `worlds/mass_effect_le1/client.py` (and the mod version in `test/test_client.py`). For a new
   setup app, `<Version>` in `tools/setup/APSetup.csproj`.
2. `python tools/release/build_release.py <version>` writes `build/release-<version>/`.
3. `python tools/setup/package.py <app version>`, then `python tools/setup/test_setup.py --exe
   "release/MassEffectAPSetup-<app version>/Mass Effect AP Setup.exe"`.
4. Write `build/release-<version>/RELEASE_NOTES.md` (the release page's text).
5. `python tools/release/publish.py --dry-run` to see the steps, then `python tools/release/publish.py`. It zips the setup app, writes `SHA256SUMS`, pushes the branch, opens and merges the pull request into `main`, tags the merge commit `v<version>`, creates the pre-release with the notes and uploads the assets, then prints the links. Run it again after a failure: it skips what is done.

## Credits

- **Original project:** [ME1-APWorld](https://github.com/Mass-Effect-LE-Archipelago/ME1-APWorld) by Jerrod
  (Unowa001): the world, the client, the game mod, the bridge and the setup app. This repository is a fork by Tyler
  Kessler; the changes since 0.2.5 (squadmates only through items, squad conversation logic, Ilos item rules, building
  the mod and releases from the repository) are in its history.
- **Contributors:** poststructural.
- [Archipelago](https://archipelago.gg): the multiworld framework (MIT).
- [ME3Tweaks](https://me3tweaks.com): Legendary Explorer (LegendaryExplorerCore, GPL-3.0, which the setup app is built
  with) and Mod Manager.
- [Trilogy Save Editor](https://github.com/KarlitosVII/trilogy-save-editor) by KarlitosVII: its Mass Effect plot
  database, used as a reference for plot flags.
- [Nexus Mods mod 2422](https://www.nexusmods.com/masseffectlegendaryedition/mods/2422): the way the galaxy-map travel
  videos are skipped.

This is an unofficial fan project. It is not affiliated with or endorsed by BioWare or Electronic Arts. You need your own
copy of Mass Effect Legendary Edition (PC).

## AI Disclosure
AI was used to aid in the research and development of this multiworld.
