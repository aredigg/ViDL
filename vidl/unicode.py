import unicodedata


class Unicode:
    @staticmethod
    def len(string):
        length = 0
        for char in string:
            if Unicode.__long(char):
                length += 2
            elif Unicode.__short(char):
                length += 1
        return length

    @staticmethod
    def __zero_size(c):
        return (
            unicodedata.category(c) in ("Mn", "Me", "Cf")
            and ord(c) != 0x200D
            or ord(c) in (0x200B, 0x200C, 0x200D, 0xFEFF)
        )

    @staticmethod
    def __short(c):
        return not (Unicode.__zero_size(c) or Unicode.__long(c))

    @staticmethod
    def __long(c):
        o = ord(c)
        return (
            0x1F300 <= o <= 0x1F9FF
            or 0x2600 <= o <= 0x26FF
            or 0x1F000 <= o <= 0x1F02F
            or 0x1F0A0 <= o <= 0x1F0FF
            or 0x1FA00 <= o <= 0x1FAFF
            or o
            in (
                0x2700,
                0x2705,
                0x270A,
                0x270B,
                0x2728,
                0x274C,
                0x274E,
                0x2753,
                0x2754,
                0x2755,
                0x2757,
                0x275F,
                0x2760,
                0x2795,
                0x2796,
                0x2797,
                0x27B0,
                0x27BF,
            )
            or unicodedata.east_asian_width(c) in ("F", "W")
        )
