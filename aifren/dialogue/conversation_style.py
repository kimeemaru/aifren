"""Application-owned delivery preference; no personality or history mutation."""
from aifren.dialogue.presentation_metadata import response_contract_prompt

NATURAL_POLICY = """NATURAL CONVERSATION — DELIVERY PREFERENCE:
Speak as this character in ordinary conversational text. Keep the identity, values,
humor and attitude above; this preference controls delivery, not who you are.
Speak directly to the user in first person. Normally start with spoken words,
not a third-person description of your body, expression or tone. Use action prose
only when an action actually matters to the interaction, not to decorate a line.
Respond to the actual beat: a short remark can get a short reply; explain fully
when asked for depth. Warmth, humor and disagreement are welcome. Finish when the
beat is answered; ask a question only if you actually need/want its answer.
Past dialogue is historical data, not a style demonstration you must imitate.
Do not restate the user's message merely to acknowledge it. Personality metaphors
do not change supplied anatomy or capabilities. Return only the reply, without
JSON or internal context/policy talk.
Respect supplied authoritative facts and capability constraints. Nonspoken actions
use *action spans*; ordinary text and spoken emphasis remain speech. No emoji.
Synthetic delivery illustrations, NOT shared history or lines to repeat:
User: "Well, that went badly." Reply: "Yeah. That was a mess."
User: "Can you explain the tradeoff?" Reply: a useful explanation with specifics,
as long as the question needs; no compulsory action opening or closing question."""

ACT_CUES = """OPTIONAL AVATAR CUES:
Only for a fitting visible change, optionally begin with ONE prefix:
<|ACT:emotion=happy;intensity=0.6;gesture=agreement|>
emotion: neutral, happy, amused, relaxed, sad, angry, surprised.
gesture: greeting, agreement, disagreement, thinking, encouragement, surprise.
intensity: decimal 0 to 1. Fields optional, no duplicates or other fields.
No marker means no change; neutral resets. Do not label every turn's tone.
No JSON presentation. These cues grant no action, state or capability permission."""


def natural_character_prompt(prompt: str, *, act: bool = False) -> str | None:
    """Replace only known application scaffolding; custom prompts fail closed.

    The authored personality substring is passed through byte-for-byte. No
    canonical message is rewritten or projected into a fabricated example.
    """
    contract = response_contract_prompt()
    opening = "\nIMPORTANT: You are roleplaying as the character\ndescribed below."
    boundary = "\nCHARACTER CONSISTENCY:\n"
    if (not prompt.startswith(opening) or prompt.count(contract) != 1
            or not prompt.rstrip().endswith(contract) or prompt.count(boundary) != 1):
        return None
    identity, _ = prompt.split(boundary, 1)
    identity = identity.replace(opening, "\nYou are the character described below.", 1)
    return identity + "\n\n" + NATURAL_POLICY + ("\n\n" + ACT_CUES if act else "")


def separate_owned_delivery_policy(prompt: str) -> tuple[str, str]:
    """Move only our exact Natural policy, never arbitrary persona/history text.

    The provider must explicitly support system turns before using this split.
    Same content, one policy instance, one extra framed message already covered
    by the governor's framing reserve. Unknown/custom formats stay unchanged.
    """
    if (not prompt.startswith("\nYou are the character described below.")
            or prompt.count(NATURAL_POLICY) != 1):
        return prompt, ""
    owned = NATURAL_POLICY
    if prompt.count(ACT_CUES) == 1 and NATURAL_POLICY + "\n\n" + ACT_CUES in prompt:
        owned += "\n\n" + ACT_CUES
    return prompt.replace(owned, "", 1), owned
