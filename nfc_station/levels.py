import math

LEVELS = (
    'Drahtentdecker', 'Funkenfänger', 'Kabelheld', 'Stromsurfer', 'Blitzjäger',
    'Voltprofi', 'Hochspannungsheld', 'Turbofinger', 'Blitzmeister', 'Drahtlegende',
)

# Midpoints preserve the existing lower-inclusive difficulty buckets.
LEVEL_OPTIONS = tuple({'level': i + 1, 'name': name, 'override': (i + .5) / 10}
                      for i, name in enumerate(LEVELS))


def override_level(fraction):
    if type(fraction) not in (float, int) or not math.isfinite(fraction) or not 0 <= fraction <= 1:
        raise ValueError('Ungültiger Geschwindigkeits-Override')
    # Lower-inclusive buckets; 100% belongs to the last level as well.
    return LEVELS[min(int(fraction * 10), 9)]
