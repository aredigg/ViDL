import re


class Error:
    BRACKET_PREFIX = re.compile(r"^\[([^\]]+)\]\s*(.*)$")

    def __init__(self, e) -> None:
        e = str(e).removeprefix("ERROR: ")
        provider, message = self.__parse_bracket(e)
        identity, message = self.__parse_colon(message)
        self.provider = provider
        self.identity = identity
        self.message = message

    def __parse_bracket(self, message: str) -> tuple[str | None, str]:
        match = Error.BRACKET_PREFIX.match(message)
        if match is None:
            return None, message
        return match.group(1), match.group(2)

    def __parse_colon(self, message: str) -> tuple[str | None, str]:
        parts = message.split(":", 1) if message.count(":") > 1 else [None, message]
        return parts[0], parts[1]
