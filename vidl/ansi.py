import re

from .unicode import Unicode


class ANSI:
    SEQUENCE_MATCH = re.compile(
        r"\x1B(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1B]*(?:\x07|\x1B\\)|[@-Z\\-_])"
    )

    ClearScreen = "\x1b[0m\x1b[2J"

    Inverse = "\x1b[7m"
    InverseReset = "\x1b[27m"

    Blink = "\x1b[5m"
    BlinkReset = "\x1b[25m"

    Bold = "\x1b[1m"
    Dim = "\x1b[2m"
    BoldReset = DimReset = "\x1b[22m"

    class Alternate:
        Enter = "\x1b[?1049h\x1b[?25l"
        Leave = "\x1b[?25h\x1b[?1049l"

    class Color:
        @staticmethod
        def __get_rgb(hex: str) -> tuple[int, int, int]:
            hex = hex.removeprefix("#")
            assert len(hex) == 6
            return int(hex[0:2], 16), int(hex[2:4], 16), int(hex[4:6], 16)

        @staticmethod
        def fg(hex: str) -> str:
            r, g, b = ANSI.Color.__get_rgb(hex)
            return f"\x1b[38;2;{r};{g};{b}m"

        @staticmethod
        def bg(hex: str) -> str:
            r, g, b = ANSI.Color.__get_rgb(hex)
            return f"\x1b[48;2;{r};{g};{b}m"

        DefaultFg = "\033[39m"
        DefaultBg = "\033[49m"

        Black = "\x1b[38;2;0;0;0m"
        Cerise = "\x1b[38;2;217;56;106m"
        BurntSienna = "\x1b[38;2;227;114;86m"
        PineGreen = "\x1b[38;2;32;109;75m"
        FashionBlue = "\x1b[38;2;36;59;211m"
        NeonChartreuse = "\x1b[38;2;217;255;47m"  # D9FF2F

    @staticmethod
    def remove(string: str) -> str:
        return ANSI.SEQUENCE_MATCH.sub("", string)

    @staticmethod
    def title_bar(string: str) -> str:
        return f"\x1b]2;{string}\x1b\\"

    @staticmethod
    def notify(string: str) -> str:
        return f"\x1b]9;{string}\x1b\\"

    @staticmethod
    def print(string: str = "", row: int = 1, col: int = 1) -> str:
        return f"\x1b[{row};{col}H{string}"

    @staticmethod
    def len(string: str) -> int:
        return Unicode.len(ANSI.remove(string))

    @staticmethod
    def trim(string: str, length: int) -> str:
        out, out_length, pos = [], 0, 0
        while pos < len(string):
            if match := ANSI.SEQUENCE_MATCH.match(string, pos):
                out.append(match.group())
                pos = match.end()
            else:
                width = Unicode.len(string[pos])
                if out_length + width > length:
                    break
                out.append(string[pos])
                out_length += width
                pos += 1
        return "".join(out)
