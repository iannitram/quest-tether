Stage: Dev Loop
Since: 2026-10-26
Kind: own
Spec: SPEC.md (approved 2026-10-26)   Roadmap: ROADMAP.md (stages: docs/code-map.md "Dev stages to 1.0")   Code map: docs/code-map.md
Platforms: windows, linux (SteamOS Desktop Mode + a Decky plugin in Game Mode); no Mac
Notes: Windows + Linux + Decky + CLI. Done: platform layer (#13, #15; #14's children #31-#34), qtetherd (#18's children #35-#38), Decky (#19, #20), udev (#17), crash reporting (#30), CLI (PR #88 for #74), #1, #2, #4, #6, #11. Left: #86, wiring #62/#64/#65/#66, #16 native relay, CLI follow-ups #74 (headset test, packaging), packaging #10/#21, MCP #75, soak #3, Windows questions #7-#9 (needs-owner-device); trackers #12, #14, #18 close as their children land. After 1.0: #5, #68. Order: #90. CI: Windows and Linux.

Stages: Proposal -> Spec -> Roadmap -> Dev Loop -> Prototype / Beta.
What each stage means and who moves a project: `PIPELINE.md` in the owner's private pipeline repo.
The Project Manager updates this file when the stage changes and adds a CHANGELOG line.
