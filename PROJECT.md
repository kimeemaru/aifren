# AIFren project direction

## Current technical baseline

AIFren is a local-first companion with permanent conversation and separately
owned memory, current state, and presentation. It remains pre-1.0 software.

Memory V2 is normal long-term-memory authority: canonical-source history,
durable facts/corrections, episodes, Active State, Open Threads and Truth Scope.
Normal operation is V1-independent. V1 is temporary explicit one-launch rollback,
with no normal V2 prompt or learned-memory/summary writes and no silent fallback.
CompanionMemoryRealizer owns the surface of admitted answers. Optional present
commentary is non-authoritative and dispensable.

The responsive Context Governor budgets the whole request: required authority,
immediate complete exchanges, admitted current continuity, older exchanges, then
optional context. Its local working target is separate from model capacity.

The normal Unity player has responsive committed speech, selectable Natural /
Roleplay delivery, opt-in ACT cues and optional CPU expressions. Automatic faces
are temporary response-owned overlays. Settings use Save/Cancel; they do not edit
personality or history. See [delivery controls](docs/NATURAL_COMPANION_DESIGN.md).

New characters own their continuity directory and database. Settings > Character >
Manage provides storage status, open folder, reviewed selected-character migration
and retained-copy cleanup, Reset timeline and Delete. UUID/revision-bound
confirmation and resumable operation journals protect ownership. Framing is
character/asset/orientation specific. Shared assets remain independently owned.

Generic object/locus application, scene controls and capability causes retain
backend authority. History and Memory Viewer/Editor are implemented. Named-topic
personal past-value questions now use source-grounded recall after restart;
ambiguous references clarify. Expired one-turn anchors are not restored.

## Known limits

- Intermittent blank History/Memory panels after reset and live switching remain
  unresolved. Synthetic reset/switch checks pass; restart recovery does not prove
  the reported live refresh issue fixed. Character/session fences remain enabled.
- Natural delivery can still be verbose, include roleplay openings or append
  unnecessary questions. Ordinary free dialogue is not generally semantically
  verified. Governed memory has a stricter admission boundary.
- Optional memory commentary uses closed present speech acts and narrow reaction
  compatibility; it is not unrestricted personality generation.
- Automatic emotion classification is conservative and may abstain or misread
  tone. ACT syntax compliance does not establish good emotional selection.
- CPU speech starts from an opening unit after commitment. Native synthesis in
  flight cannot be forcibly preempted; replacement may wait for that unit.
- Recent Pulse and lean experiments stay disabled; impulses are dormant with no
  automatic producers. Relationship State is not implemented.

## Release-candidate retirement gate

Before retiring V1 runtime/rollback/import/rolling-summary code and one-time
legacy shared-store migration/cleanup tools, verify retained development data and
outstanding application copies are handled. Migration is not proof of retained-copy
cleanup. Cleanup is not confirmed merely by this source update.

Retirement requires a separate reviewed change. Preserve canonical history, valid
V1-origin records already admitted to V2, ordinary Reset/Delete, continuing
operation recovery and future V2 schema upgrades. No live-data cleanup or migration
is performed by this catch-up.

## Next work

1. Resolve demonstrated continuity/refresh defects and improve companion feel.
2. Polish avatar, animation, speech and audio presentation.
3. Finish character/avatar backup and export workflows.
4. Windows and distribution packaging.
5. Release-candidate hardening and the retirement gate above.
6. Later Relationship State and controlled external capabilities.

Development continues in this public repository as one application codebase.
Ship reviewed source, synthetic regressions and necessary docs together; see
[the developer guide](docs/DEVELOPER_GUIDE.md). This status does not claim human
subjective acceptance, universal grounding or a consumer 1.0 release.
