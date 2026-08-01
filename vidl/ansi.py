import re

from .unicode import Unicode


class ANSI:
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
        def __get_rgb(hex):
            hex = hex.removeprefix("#")
            assert len(hex) == 6
            return int(hex[0:2], 16), int(hex[2:4], 16), int(hex[4:6], 16)

        @staticmethod
        def fg(hex):
            r, g, b = ANSI.Color.__get_rgb(hex)
            return f"\x1b[38;2;{r};{g};{b}m"

        @staticmethod
        def bg(hex):
            r, g, b = ANSI.Color.__get_rgb(hex)
            return f"\x1b[48;2;{r};{g};{b}m"

        DefaultFg = "\033[39m"
        DefaultBg = "\033[49m"

        Cerise = "\x1b[38;2;217;56;106m"
        BurntSienna = "\x1b[38;2;227;114;86m"
        PineGreen = "\x1b[38;2;32;109;75m"
        FashionBlue = "\x1b[38;2;36;59;211m"
        NeonChartreuse = "\x1b[38;2;217;255;47m"  # D9FF2F

    @staticmethod
    def remove(string):
        return re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]").sub("", string)

    @staticmethod
    def title_bar(string):
        return f"\x1b]2;{string}\x1b\\"

    @staticmethod
    def notify(string):
        return f"\x1b]9;{string}\x1b\\"

    @staticmethod
    def print(string="", row=1, col=1):
        return f"\x1b[{row};{col}H{string}"

    @staticmethod
    def len(string):
        return Unicode.len(ANSI.remove(string))

    @staticmethod
    def trim(string, length):
        regex = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
        pos = 0
        escapes = []
        while pos < len(string):
            match = regex.match(string, pos)
            if match:
                escapes.append((match.group(), pos))
                pos = match.end()
            else:
                pos += 1
        string = regex.sub("", string)
        while ANSI.len(string) > length:
            string = string[:-1]
        for escape, pos in escapes:
            if pos < len(string):
                string = string[:pos] + escape + string[pos:]
            else:
                string += escape
        return string
