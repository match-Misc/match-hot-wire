import math

LEVELS = (
    'Drahtentdecker', 'Funkenfänger', 'Kabelheld', 'Stromsurfer', 'Blitzjäger',
    'Voltprofi', 'Hochspannungsheld', 'Turbofinger', 'Blitzmeister', 'Drahtlegende',
)


def override_level(fraction):
    if type(fraction) not in (float, int) or not math.isfinite(fraction) or not 0 <= fraction <= 1:
        raise ValueError('Ungültiger Geschwindigkeits-Override')
    # Lower-inclusive buckets; 100% belongs to the last level as well.
    return LEVELS[min(int(fraction * 10), 9)]
