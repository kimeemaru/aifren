# Stock English pronunciation fallback

The stock American-English Kokoro path keeps Misaki's dictionary, number
normalization, sentence analysis and punctuation. Its unfamiliar-word fallback
uses only the CMU Flite letter-to-sound decision trees. This is not the Flite
speech engine: there is no Flite voice, native library, subprocess or new neural
model. Lookup and tree traversal are non-neural host preprocessing; the selected
Kokoro neural model retains its independently verified execution device.

The actual source/data is [festvox/flite](https://github.com/festvox/flite), revision
`6c9f20dc915b17f5619340069889db0aa007fcdc`: `lang/cmulex/cmu_lts_model.c`,
`cmu_lts_model.h` and `cmu_lts_rules.c`. These files use the permissive Carnegie
Mellon terms in [COPYING](https://github.com/festvox/flite/blob/6c9f20dc915b17f5619340069889db0aa007fcdc/COPYING);
none is listed among its exceptions. The lexicon/LTS authors are Alan W Black
and Kevin A. Lenzo. AIFren's project licence is unchanged.

`aifren/tts/flite_lts.py` is an original bounded Python reader/evaluator of the
reviewed format/context semantics. Changes from upstream are offline identity
validation, Python evaluation, Misaki phone conversion, explicit errors and a
bounded in-process cache. No GPL configure/build helpers are used or shipped.
Preserve the CMU copyright, conditions, disclaimer, authors and this modification
notice with the selected source/data. Include the pinned sources and conversion
recipe with distribution materials so the converted resource is reviewable.

The lossless compact JSON resource contains 25,505 six-byte states, the original
phone table and 26 roots. It is 306,767 bytes, SHA-256
`1020599f803c75641ca680208bd06cc5d0e5e31ed2742e59086542838466b1b7`.
Runtime neither downloads it nor rebuilds character/pronunciation caches.

Composite tokens such as clock times return to Misaki's existing subtoken/number
lexicon. Unresolved or malformed values fail visibly instead of losing a word.
Known dictionary words retain their original pronunciation. Unfamiliar names,
loan words and stress placement can still be wrong; no general pronunciation or
acoustic-equivalence guarantee follows from phoneme coverage. This resource is
American-English only, matching the included stock voice.

Upstream Kokoro eagerly imports the optional GPL phonemizer/eSpeak fallback. The
reviewed package integration must avoid that import as well as replace the fallback
callable. Replacing it after the unmodified constructor runs is insufficient.
Kokoro itself is not GPL. This component review does not clear unrelated codecs,
native libraries or model weights.
