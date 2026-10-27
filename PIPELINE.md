Stage: Dev Loop
Since: 2026-10-26
Kind: own
Spec: SPEC.md (approved 2026-10-26)   Roadmap: docs/code-map.md "Dev stages to 1.0"   Code map: docs/code-map.md
Platforms: windows, linux (SteamOS Desktop Mode + a Decky plugin in Game Mode); no Mac
Notes: Windows-only app becoming Windows + Linux + Decky: platform layer #13 (Windows) -> #14 (Linux) -> #15 (switch callers), then #16 native relay, #17 udev, #18 qtetherd service (Hard), #19/#20 Decky, #21 Linux package. Start with #11, #1, #4. Device checks (#2, #3, #6-#9) need the headset (needs-owner-device). CI: Windows and Linux.

Stages: Proposal -> Spec -> Roadmap -> Dev Loop -> Prototype / Beta.
What each stage means and who moves a project: `PIPELINE.md` in the owner's private pipeline repo.
The Project Manager updates this file when the stage changes and adds a CHANGELOG line.
