import base64
from typing import Optional
from forest.utils import logging


class Message:
    """
    Base message type

    Attributes
    -----------
    blob: dict
       blob representing the jsonrpc message
    """

    text: str

    def __init__(self, blob: dict) -> None:
        self.blob = blob
        # parsing
        self.command: Optional[str] = None
        self.tokens: Optional[list[str]] = None
        if self.text and self.text.startswith("/"):
            command, *self.tokens = self.text.split(" ")
            self.command = command[1:]  # remove /
            self.arg1 = self.tokens[0] if self.tokens else None
            self.text = " ".join(self.tokens)

    def to_dict(self) -> dict:
        """
        Returns a dictionary of message instance
        variables except for the blob
        """
        properties = {}
        for attr in dir(self):
            if not (attr.startswith("_") or attr in ("blob", "full_text")):
                val = getattr(self, attr)
                if val and not callable(val):
                    # if attr == "text":
                    #    val = termcolor.colored(val, attrs=["bold"])
                    #    # gets mangled by repr
                    properties[attr] = val

        return properties

    def __getattr__(self, attr: str) -> None:
        # return falsy string back if not found
        return None

    def __repr__(self) -> str:
        return f"Message: {self.to_dict()}"


class AuxinMessage(Message):
    def __init__(self, blob: dict) -> None:
        if "id" in blob:
            self.id = blob["id"]
            blob = blob.get("result", {})
        else:
            self.id = None
        logging.info("msg id: %s", self.id)
        self.timestamp = blob.get("timestamp")
        content = blob.get("content", {})
        msg = (content.get("source") or {}).get("dataMessage") or {}
        self.text = self.full_text = msg.get("body") or ""
        self.attachments = msg.get("attachments")
        self.group = msg.get("group") or msg.get("groupV2")
        maybe_quote = msg.get("quote")
        self.quoted_text = "" if not maybe_quote else maybe_quote.get("text")
        self.source = (
            blob.get("remote_address", {}).get("address", {}).get("Both", [""])[0]
        )
        payment_notif = (
            (msg.get("payment") or {}).get("Item", {}).get("notification", {})
        )
        if payment_notif:
            receipt = payment_notif["Transaction"]["mobileCoin"]["receipt"]
            self.payment = {
                "note": payment_notif.get("note"),
                "receipt": base64.b64encode(bytes(receipt)).decode(),
            }
        else:
            self.payment = {}
        if self.text:
            logging.info(self)  # "parsed a message with body: '%s'", self.text)
        super().__init__(blob)


class StdioMessage(Message):
    """Represents a Message received from signal-cli, optionally containing a command with arguments."""

    def __init__(self, blob: dict) -> None:
        super().__init__(blob)
        self.envelope = envelope = blob.get("envelope", {})
        # {'envelope': {'source': '+15133278483', 'sourceDevice': 2, 'timestamp': 1621402445257, 'receiptMessage': {'when': 1621402445257, 'isDelivery': True, 'isRead': False, 'timestamps': [1621402444517]}}}

        # envelope data
        self.source: str = envelope.get("source")
        self.name: str = envelope.get("sourceName") or self.source
        self.timestamp = envelope.get("timestamp")

        # msg data
        msg = envelope.get("dataMessage", {})
        self.full_text = self.text = msg.get("message", "")
        self.group: Optional[str] = msg.get("groupInfo", {}).get("groupId")
        self.quoted_text = msg.get("quote", {}).get("text")
        self.payment = msg.get("payment")
        # self.reactions: dict[str, str] = {}
