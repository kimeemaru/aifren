# Developer guide

## Source navigation

Application implementation is in [`aifren`](../aifren); the
[source map](SOURCE_LAYOUT.md) lists owners, moves and supported commands.
The root backend/console/TTS-smoke files are thin compatibility commands.
Use explicit package imports in source, tests, patches and subprocess snippets.

## Setup and normal Linux workflow

Use Python 3.10–3.12 and Unity **2022.3.62f3**. The project pins UniVRM and Unity
package versions in `unity/AIFrenUnityPoc/Packages`. Keep normal host licensing,
HOME/XDG and graphics; isolate test application data instead of hiding the Editor's
environment. No private character, seed, font pack or local motion is required.

On a supported Linux development host, review and run:

```bash
scripts/setup_aifren_runtime_linux.sh
.venv-aifren/bin/python backend_host.py
```

Setup installs dependencies and may download runtime tools/models; it reports
missing system prerequisites for you to install. Review its platform requirements
before running it. It builds the managed llama.cpp dependency with a portable CPU
baseline and detected CUDA support when available. Downloaded model weights remain
ignored local inputs with their own licenses. MiniLM expects an installed
`models/all-MiniLM-L6-v2` directory and uses local-only loading. Kokoro/STT resources
are installed separately by setup, not bundled in the source repository.

The loopback endpoint defaults to `ws://127.0.0.1:8765`. With no character files,
startup creates local application metadata and offers character creation; it
does not need a tracked conversation. Deleting the last identity leaves an empty
library, not a recreated legacy timeline. Missing provider configuration is recoverable
and Settings remains available. Configure a local model or your own online provider
credentials through Settings. Never commit the resulting configuration or data.

Build and start the same normal client:

```bash
scripts/aifren_dev_linux.sh rebuild development
scripts/aifren_dev_linux.sh current development
```

The optional developer Tk control window is launched by
`scripts/aifren_dev_launcher_linux.py`; install its application entry with
`scripts/install_aifren_dev_launcher_linux.sh`. It is tooling, not another companion
frontend. The two ordinary start actions are **Start Development Build** and
**Rebuild Development + Start**, both targeting `Builds/LinuxDevelopment` and the
current backend. Stop and retained controls remain. Resets are deliberate,
unchecked and one-shot. Failed builds preserve the last completed target; a failed
rebuild must not silently report an older client as new. There is no release-target
fallback for Development starts.

## Memory authority

Ordinary launches select V2. The explicit one-launch rollback command is:

```bash
scripts/aifren_dev_linux.sh current development v1-memory
```

The next ordinary launch returns to V2. A direct backend process can explicitly set
`AIFREN_MEMORY_AUTHORITY=v1`; do not persist this as an accidental default. Rollback
does not open/mutate V2. A V2 error never silently selects V1. Normal V2 has no V1
prompt, learned-memory or summary writes.

Normal startup uses bounded existing observation/index/episode owners; no private
prepared seed is needed. Catch-up is idempotent/resumable and preserves current
facts, Viewer corrections, state, threads and scopes. Do not replace live SQLite
with a derived cache or reconstruct original administrative evidence from summary
text. See [the store guide](../aifren/memory_v2_store/README.md).

CompanionMemoryRealizer consumes only an admitted answer. Keep retrieval and truth
validation outside it. Its optional present reaction cannot alter the grounded
core; failed reactions are dropped without another inference or repair. Emergency
responses remain separately observable internal paths.

## Public-safe backend validation

From this checkout, use the installed environment. The structural runner performs
unittest discovery with isolated temporary application state, offline flags and
explicit toy embedding/runtime substitutes. It does not certify embedding quality.

```bash
.venv-aifren/bin/python scripts/run_structural_tests.py
.venv-aifren/bin/python scripts/check_offline_embeddings.py
.venv-aifren/bin/python test_tts.py
git diff --check
```

The embedding check requires the separately installed MiniLM directory and fails
clearly when it is missing. TTS smoke checks provider construction/fallback contracts
without a microphone, audio device or downloads; it is not acoustic validation.
For targeted synthetic fixtures use `python -m unittest discover -s tests -p
'<test_module>.py' -v` with your test-owned working data/configuration. Do not run
integration fixtures against a real character merely because source is available.

Coverage includes fresh/partial/current V2, interrupted catch-up, correction/state/
thread/scope preservation, default and rollback authority, no silent V1 fallback,
source ordering and exact follow-up, realizer surfaces, cancellation and transport.
Structural results and installed real-model checks must be reported separately.

## Unity tests and builds

Open `unity/AIFrenUnityPoc` with the pinned Editor. Unity restores the pinned packages
on first import. The already-published licensed sample avatar and generic project
background/audio remain available; custom avatars/backgrounds are local imports.
No additional animation pack or original trial file is bundled. Missing optional
clips use the existing procedural behavior. Public builds verify the bundled sample
identity and reject unreviewed local presentation inputs.
The build preflight also requires imported humanoid geometry. It retries a failed
first import after package shaders are available, then fails visibly if the bundled
avatar still cannot load; a successful empty-avatar build is not sufficient.

```bash
UNITY_EDITOR="$UNITY_EDITOR" scripts/build_aifren_linux.sh --preflight
"$UNITY_EDITOR" -batchmode -projectPath "$PWD/unity/AIFrenUnityPoc"   -runTests -testPlatform EditMode -testResults "$PWD/native-tests.xml"   -logFile "$PWD/native-tests.log"
UNITY_EDITOR="$UNITY_EDITOR" scripts/build_aifren_linux.sh --development
```

Set `UNITY_EDITOR` to your installed executable. Do not add `-quit` to the test
command; require completed XML. Keep results/builds outside version control.
Run the preflight early in a native work session: it executes the actual project's
asset checks and requires a fresh completion marker, without replacing the player.
An Editor version string or standalone C# compilation is not this execution gate.
If licensing fails, restore sign-in/entitlement through the normal Unity controls;
do not change HOME/XDG or carry offline application-test namespaces into the Editor.
Finite native fixtures establish `PresentationPreferences` isolation before first
access/Awake/Start. Never borrow normal keys and restore a snapshot at exit, clear
normal preferences or overwrite concurrent user edits. Engine/window state must
also use the existing isolation. Settings snapshots, if needed, remain private.

Validate on the intended portrait display and a smaller landscape sample without
changing global monitor topology or framing. Capture only the owned application.
Use synthetic capture and app-local quiet audio; never unattended microphone input.
Capture-free performance samples and screenshot readback measurements are distinct.
Stop only processes demonstrably owned by the finite test runner.

## Maintenance guardrails

AssistantService owns canonical commit/publication and audio cancellation. Unity
requests operations and renders bounded snapshots. Preserve character/scope/request
identities through retries, reconnect and delayed load. History and Viewer are
paged derived views; hidden updates mark dirty instead of rebuilding hierarchies.

Models/providers stay replaceable. Managed llama.cpp uses `--logits_all false` and
only owned workers may be stopped; external compatible endpoints remain external.
Release diagnostics are off by default and bounded when enabled. Do not publish
settings, credentials, character records, generated speech, screenshots or logs.
Windows/package helpers remain preliminary; source validation is not a certified
portable runtime or 1.0 release. Review [distribution boundaries](DISTRIBUTION_ASSET_MANIFEST.md)
before bundling dependencies or user-supplied material.

## Current controls and storage

[Delivery settings](NATURAL_COMPANION_DESIGN.md) use Save/Cancel: Roleplay default,
responsive speech on, ACT preview and automatic CPU expressions off. Natural is
selectable guidance with known narration/question limitations. Install optional
expression dependencies/model explicitly; no turn silently downloads weights.

The character voice integration adds **Audio > Character voice**: engine,
reference WAV, transcript, language, Prepare, Preview/Stop and explicit Save/Cancel.
See [character voices](CHARACTER_VOICE.md) for the reviewed runtime and ownership
boundary. It requires a client built from the matching source. CPU reference
conditioning is an opt-in alternative, not a latency replacement for Kokoro.
**Appearance > Body performance** adds optional subtle breathing/attention and
semantic motion previews. It defaults off and uses the existing animation owner.

Settings > Character > Manage previews one name/UUID/revision and operation scope.
New characters use character-local continuity. Existing legacy layouts require
confirmed selected migration; the tool verifies original records and reports retained
copies. Retained-copy cleanup is a separate confirmed operation. Reset keeps profile,
avatar/framing and preferences while replacing learned continuity; Delete removes
owned identity/preferences but preserves shared assets. Interrupted work remains
explicit/resumable. Never use file disappearance as permission to restore a timeline.

The future V1/legacy-tool retirement gate is in PROJECT.md. Migration alone does
not confirm retained-copy cleanup. Preserve valid V1-origin records in V2, canonical
history, ordinary management/recovery and future schema upgrades during retirement.

### Local distribution candidates

Linux and Windows selectors now share one explicit source/resource inventory.
Neither accepts a live environment or character directory as a recursive input.
Stage reviewed, platform-matching files separately and pass a JSON list of
`{"path":"package/relative/file","sha256":"..."}` records:

```bash
python scripts/package_linux.py --source-root . --staging-root /path/to/staging \
  --inputs /path/to/reviewed-inputs.json --output /path/to/fresh-candidate \
  --default-model reviewed-included-model.gguf
# Windows uses package_unity.py with the same arguments and optional --archive.
```

The optional default must name an included, reviewed GGUF; no developer model or
settings are discovered. Generated seed settings select local operation. Packages
scrub inherited development overrides and launch the bundled interpreter in
isolated mode. `--portable` keeps private state under the package's `UserData`;
otherwise the per-user application-data location is used. The matching client
uses a typed preference file in that data directory, without changing HOME/XDG or
host Unity preferences. Older clients are rejected before launch.

A selected inventory is not a working platform certification. Validate native
libraries, licenses, offline models, a fresh extraction, restart and directory move.
Windows continuity locking now uses native shared/exclusive locks and rejects
reparse-point/hardlink substitution; the POSIX path keeps its existing guards.
The pinned Windows dependency list and public-code-only `windows-portable-check`
workflow assemble an embedded interpreter and exercise synthetic backend ownership
and recall on a native runner. The assembled candidate includes a matching client
and local resources, but native graphical/audio/GPU acceptance remains separate.
Cross-compilation or Wine alone does not certify those behaviors. See the current
distribution manifest for the unresolved speech-license and transitive-binary
clearance gates; do not promote an archive while they remain open.

### History and Memory recovery

History has **Refresh** in its header. Memory uses its existing query button:
**Search** when the query changes, **Refresh** otherwise, and **Retry** after a
failed or timed-out read. The lane, status and scope filters remain visible; an
empty V1 archive or filtered page says nothing about all V2 memory. Loading,
successful empty results and unavailable/15-second timeout states are distinct.
Refresh preserves same-binding History navigation and unchanged-record editor
drafts. Changing character or timeline retires them. A last successful page can
remain visible during a same-owner retry and is explicitly labelled as such.

Refresh does not invoke generation, import/rebuild memory, modify canonical data or
restart the backend. If disconnected/unbound it uses the normal connection/snapshot
handshake. There is no unbounded automatic retry loop. Development flight recording
includes read counts, request aliases, fence rejection stages and deferred/rendered
model counts; use its existing incident/manual dump for blank-view reports. Finite
synthetic QA can deliberately drop one response to prove timeout/Retry. This does
not reproduce or establish the root cause of the intermittent reset/switch report.

## Separate application source and existing runtime data

The launcher loads its ignored `.env` before starting the backend. Supported local
choices can keep resources/data in place when changing source checkout:

```bash
AIFREN_PYTHON=/path/to/existing-environment/bin/python
AIFREN_RESOURCE_ROOT=/path/to/existing-resources
AIFREN_DATA_ROOT=/path/to/existing-application-data
```

Paths are examples, not required directories. This does not migrate or clone data.
The selected interpreter supplies dependencies; backend/scripts and Unity inputs
remain in the active checkout. The existing runtime-layout owner resolves the
source default independently of CWD and external resources/data. An absolute
`backend_host.py` command works from outside the checkout without initializing
a different application root. Do not resolve a venv Python symlink to the system
interpreter. Avoid PYTHONPATH entries pointing at another application implementation.
Reinstall the existing developer desktop entry from the new checkout to retarget
both actions together. Keep machine-specific configuration outside commits.

The governor's default complete-request target is 4,608 tokens, independent of
16,384 default managed capacity. It is a measured deployment starting point, not a
universal optimum. Mandatory/final local content token counts use estimated framing;
without tokenization the conservative labelled path remains. Required overflow is
visible. `AIFREN_CONTEXT_GOVERNOR=0` changes composition for one process only; it
does not change memory authority. No model allocation/output limit is reduced.

## Forward development

This repository is the active application source. Commit source, synthetic
regressions and necessary docs together. Use a branch for incomplete work and land
completed reviewed slices on main. Check privacy across every outgoing commit as
well as staged files. Never include runtime/private QA material, assets without
permission, credentials or local configuration. Historical internal archives remain
archives, not a second application branch to export periodically.

Run targeted checks while developing and the affected final suite once when stable.
Keep reports outside Git. Unchanged evidence can be reused only for unchanged owners.
The reset/switch blank-panel issue remains a known intermittent report; do not waive
session fences or claim a passing synthetic scenario fixes an unreproduced incident.
