"""Parse raw server-reported geographic metadata from Battlelog server pages.

No network or persistence operations here: safe to test independently.
"""
import json
import re
from html import unescape


def parse_battlelog_geography(html: str, guid: str) -> dict[str, str | None]:
    """Extract fields from the embedded server object matching the requested GUID.

    Do not infer geographic country from ping site or server region.
    """
    normalized = str(guid).lower().strip()
    if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', normalized):
        raise ValueError('Invalid server GUID')
    source = unescape(str(html))
    # Match a server object containing the requested GUID, then extract its
    # bounded adjacent metadata; never take the first arbitrary server object.
    matches = list(re.finditer(r'"guid"\s*:\s*"' + re.escape(normalized) + r'"', source, re.I))
    if not matches:
        raise ValueError('Requested server GUID not found on Battlelog page')
    for match in matches:
        segment = source[match.end():match.end()+20000]
        # Never consume metadata belonging to a subsequent embedded server.
        next_guid = re.search(r'"guid"\s*:\s*"[0-9a-fA-F-]{36}"', segment)
        if next_guid:
            segment = segment[:next_guid.start()]
        # The canonical server object ends after its pingSite/country pair.
        geo = re.search(r'"pingSite"\s*:\s*("(?:\\.|[^"\\])*"|null)\s*,\s*"country"\s*:\s*("(?:\\.|[^"\\])*"|null)', segment)
        if geo is None:
            continue
        ping, country = (json.loads(x) for x in geo.groups())
        region_match = re.search(r'"region"\s*:\s*(\d+|"(?:\\.|[^"\\])*"|null)', segment[:geo.start()])
        region = json.loads(region_match.group(1)) if region_match else None
        return {'country_code': str(country).lower()[:8] if country else None,
                'ping_site': str(ping).lower()[:32] if ping else None,
                'region': str(region)[:64] if region is not None else None,
                'location': None}
    raise ValueError('Geographic metadata absent for requested server')
