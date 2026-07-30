import re


class ANSI:
    class Alternate:
        Enter = "\x1b[?1049h\x1b[?25l"
        Leave = "\x1b[?25h\x1b[?1049l"

    class Color:
        @staticmethod
        def __get_rgb(hex):
            hex.removeprefix("#")
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
    def print(col, row, string):
        return f"\x1b[{row};{col}H{string}"
