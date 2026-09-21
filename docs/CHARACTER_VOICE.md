# Character voices

Implementation status: the backend has been exercised with real local speech.
The new client controls still require a matching Unity build and native validation;
the validation host lacked a valid Editor entitlement. Do not treat this branch as
a completed portable player or a voice-similarity guarantee.

Kokoro remains the default responsive speech engine. The character voice controls
in **Settings → Audio → Character voice** also support an explicitly installed,
reference-conditioned GPT-SoVITS v2ProPlus runtime on CPU. This is zero-shot
conditioning, not training. It uses more memory and preparation time than Kokoro.

Choose an engine, select a **3–10 second uncompressed PCM WAV**, enter exactly its
transcript, and choose its language. Prepare conditions the reference audio and
transcript features before reporting ready; it does not synthesize a test reply.
Preview plays
a fixed line; Stop cancels pending playback. **Save voice** applies the draft to
the selected character. Cancel restores that character's saved draft. These voice
actions do not save unrelated presentation settings or create conversation/memory.

A missing runtime or failed reference produces an explicit error. The application
does not silently substitute another voice. Choosing and saving Kokoro is the
explicit recovery path, including for a damaged saved voice profile. A saved clone
is not proof of successful conditioning; Prepare/Preview checks the installed
runtime. Conditioning is cached in process and rebuilt after runtime restart.
Changing the reference, transcript or language invalidates that conditioning.
Ready does not promise instantaneous speech: target-text processing and native
audio synthesis still occur for each committed unit. PCM currently arrives after
the complete unit; stopping playback does not make its native worker free early.

## Installation boundary

The reviewed adapter targets the official [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)
source revision `48b1a0169a28582a8984402f82cf438d3bfa6aca`, its v2ProPlus model and
an independently installed environment. The code is MIT licensed; model and
dependency notices must be reviewed separately before distributing a resource pack.
No runtime or voice weights are embedded in this source repository.

After installing those reviewed inputs in a separate environment, register them:

```sh
python scripts/register_clone_runtime.py --root /path/to/GPT-SoVITS \
  --python /path/to/isolated/python \
  --output /path/to/resources/runtimes/gpt-sovits/runtime.json
```

Registration verifies the source revision and records source/model digests. It
does not install packages, accept licenses, train a voice, or change chat-model
selection. Runtime startup verifies those inputs before importing the external
code. The CPU worker uses private process pipes, an instance nonce and one
synthesis lane. It opens no network listener. Stop invalidates work immediately;
an already running native synthesis call may still need to finish.

## Ownership and speech

`CharacterVoiceProfiles` uses the existing character resolver. Authored settings
are versioned in `voice_profile.json`; imported copies use content-addressed files
under the character's `voice/` directory. External originals remain user-owned.
Reset learned continuity preserves these choices. Confirmed character deletion
removes its owned files and association, not external recordings or other voices.
Reference, transcript, language, model revision and settings determine conditioning
identity. Character/session/profile generations fence preparation, preview and
playback; a retired result cannot play for a new owner.

The selected provider shares the existing continuous PCM playback owner. Only
accepted, canonically committed, speech-projected text enters reply synthesis.
Kokoro keeps its existing unit policy; cloning uses larger sentence groups to
amortize conditioning costs. One utterance retains ordered text/sample offsets,
subtitles and lip sync. A late chunk failure ends with an error rather than
replaying earlier words or reporting normal completion. Neither voice controls nor
preview create factual authority or companion actions.

Clone similarity, prosody and latency depend on the recording and hardware. A
successful PCM callback is not an acoustic quality assessment. Private reference
audio, transcripts, conditioning and generated speech are excluded from source,
public test fixtures, diagnostics, demonstrations and distribution packages.
