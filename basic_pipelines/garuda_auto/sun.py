"""Sunrise and sunset for the house, worked out from its latitude and longitude.

The NOAA approximation (good to a minute or two away from the poles): no
network, no library. Times come back as local HH:MM for the day `now` falls
in; where the sun does not rise or set that day there is no time.
"""
import math
import time

ZENITH = 90.833         # the sun's centre, with refraction and its own radius


def _minutes(lat, lon, day_of_year, rising):
    g = 2 * math.pi / 365 * (day_of_year - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    phi = math.radians(lat)
    cos_ha = (math.cos(math.radians(ZENITH)) / (math.cos(phi) * math.cos(decl))
              - math.tan(phi) * math.tan(decl))
    if not -1 <= cos_ha <= 1:
        return None
    ha = math.degrees(math.acos(cos_ha))
    return 720 - 4 * (lon + (ha if rising else -ha)) - eqtime        # minutes after UTC midnight


def times(lat, lon, now=None):
    """{"sunrise": "HH:MM", "sunset": "HH:MM"} in local time; a key is missing
    on a day without that event."""
    now = time.time() if now is None else now
    lt = time.localtime(now)
    offset = lt.tm_gmtoff / 60
    out = {}
    for name, rising in (("sunrise", True), ("sunset", False)):
        m = _minutes(lat, lon, lt.tm_yday, rising)
        if m is not None:
            m = int(round(m + offset)) % 1440
            out[name] = f"{m // 60:02d}:{m % 60:02d}"
    return out


def facts(lat, lon, now=None):
    """What a shortcut may test: sunrise, sunset, and sun (up or down)."""
    now = time.time() if now is None else now
    out = times(lat, lon, now)
    lt = time.localtime(now)
    hhmm = f"{lt.tm_hour:02d}:{lt.tm_min:02d}"
    if "sunrise" in out and "sunset" in out:
        rise, set_ = out["sunrise"], out["sunset"]
        up = rise <= hhmm < set_ if rise <= set_ else (hhmm >= rise or hhmm < set_)
        out["sun"] = "up" if up else "down"
    return out


def shift(hhmm, minutes):
    h, m = map(int, hhmm.split(":"))
    t = (h * 60 + m + int(minutes)) % 1440
    return f"{t // 60:02d}:{t % 60:02d}"


def valid_location(lat, lon):
    return (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
            and not isinstance(lat, bool) and not isinstance(lon, bool)
            and -90 <= lat <= 90 and -180 <= lon <= 180)
