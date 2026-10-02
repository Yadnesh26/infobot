from dataclasses import dataclass


@dataclass
class InboundMessage:
    wamid: str
    sender: str
    type: str
    text: str | None = None
    media_id: str | None = None
    media_mime_type: str | None = None
    caption: str | None = None
    forwarded: bool = False
    frequently_forwarded: bool = False
    is_reaction: bool = False
    reaction_emoji: str | None = None
    reaction_target_wamid: str | None = None
    button_id: str | None = None  # id of a tapped reply button (type "interactive")


def extract_messages(payload: dict) -> list[InboundMessage]:
    """Flatten a Meta webhook payload into a list of InboundMessage.

    A single webhook POST can carry multiple entries/changes/messages at once.
    Non-message changes (e.g. status updates) are skipped.
    """
    out: list[InboundMessage] = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                out.append(_parse_one(msg))

    return out


def _parse_one(msg: dict) -> InboundMessage:
    msg_type = msg.get("type", "")
    context = msg.get("context", {})

    if msg_type == "reaction":
        reaction = msg.get("reaction", {})
        return InboundMessage(
            wamid=msg["id"],
            sender=msg["from"],
            type="reaction",
            is_reaction=True,
            reaction_emoji=reaction.get("emoji"),
            reaction_target_wamid=reaction.get("message_id"),
        )

    if msg_type == "interactive":
        reply = msg.get("interactive", {}).get("button_reply", {})
        return InboundMessage(wamid=msg["id"], sender=msg["from"], type="interactive", button_id=reply.get("id"))

    text = None
    media_id = None
    media_mime_type = None
    caption = None

    if msg_type == "text":
        text = msg.get("text", {}).get("body")
    elif msg_type in ("image", "audio", "video"):
        media = msg.get(msg_type, {})
        media_id = media.get("id")
        media_mime_type = media.get("mime_type")
        caption = media.get("caption")

    return InboundMessage(
        wamid=msg["id"],
        sender=msg["from"],
        type=msg_type,
        text=text,
        media_id=media_id,
        media_mime_type=media_mime_type,
        caption=caption,
        forwarded=bool(context.get("forwarded", False)),
        frequently_forwarded=bool(context.get("frequently_forwarded", False)),
    )
