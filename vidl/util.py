class Util:
    @staticmethod
    def format_seconds(seconds, include_seconds=True, two_parts=True):
        if seconds < 0:
            seconds = -seconds
        hr = int(seconds / 3600)
        mn = int((seconds / 60) % 60)
        sc = int(seconds % 60)
        if include_seconds:
            if two_parts and hr == 0:
                return f"{mn:02}:{sc:02}"
            elif not two_parts:
                return f"{hr:02}:{mn:02}:{sc:02}"
        if sc > 0:
            if mn < 59:
                mn += 1
            else:
                hr += 1
                mn = 0
        return f"{hr:02}:{mn:02}"
