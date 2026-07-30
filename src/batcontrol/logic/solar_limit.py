"""Solar feed-in limit ("solar_cap") peak-shaving rule.

Pure functions for the clip-absorption rule described in
docs/development/solar-limit-evaluation.md. The rule works on the existing
forecast arrays (Wh per interval, index 0 = current interval) and produces
two outputs per evaluation:

    floor_w: minimum PV charge rate (W) the battery must be *permitted* to
             sustain right now to absorb power above the feed-in limit
             ("clip" energy that would otherwise be curtailed and lost).
             With a greedy-charging inverter a floor never forces charging
             that does not exist -- it only raises the applied cap, and the
             inverter charges ``min(actual surplus, cap)``.
    cap_w:   an upper limit on the PV-to-battery charge rate, either to
             reserve free battery capacity ahead of an upcoming clip window
             ("reservation") or to keep some capacity free while already
             inside the clip window. ``-1`` means no cap, ``0`` blocks PV
             charging entirely.

This module bakes in the settled semantics from the evaluation (headroom
applied to the forecast surplus, floor computed from the headroom-adjusted
clip) -- see the "Forecast-error plan" section of the linked document.
"""
import numpy as np


# pylint: disable=too-many-arguments,too-many-positional-arguments
# pylint: disable=too-many-locals,too-many-return-statements
def compute_solar_limit(
        production_wh, consumption_wh, feed_in_limit_w,
        interval_h, free_capacity_wh, max_capacity_wh,
        headroom=1.0, slot0_hours=None):
    """Compute the solar-cap rule output (floor, cap) for the current slot.

    Args:
        production_wh: forecast PV energy per slot (Wh), index 0 = now.
        consumption_wh: forecast consumption per slot (Wh).
        feed_in_limit_w: grid feed-in power limit in W. <= 0 or None makes
            the rule neutral (no effect).
        interval_h: slot length in hours (e.g. 0.25 or 1.0).
        free_capacity_wh: battery free capacity (Wh).
        max_capacity_wh: battery max capacity (Wh).
        headroom: safety factor >= 1.0 applied to the forecast surplus
            before the clip is computed (reservation and floor sizing).
            Forecasts systematically underestimate PV peaks; headroom
            reconstructs the higher real curve. Default 1.0 (neutral).
        slot0_hours: remaining hours in the current (partial) slot.
            Defaults to ``interval_h``.

    Returns:
        (floor_w, cap_w): both ints. ``floor_w`` of 0 means no floor.
        ``cap_w`` of -1 means no cap, 0 blocks PV charging.
    """
    if feed_in_limit_w is None or feed_in_limit_w <= 0:
        return 0, -1
    if slot0_hours is None:
        slot0_hours = interval_h

    n = min(len(production_wh), len(consumption_wh))
    # Production window ends at the first slot with zero production (same
    # convention as the existing time/price peak-shaving rules).
    prod_end = n
    for i in range(n):
        if float(production_wh[i]) == 0:
            prod_end = i
            break
    if prod_end == 0:
        return 0, -1

    slot_h = np.full(prod_end, interval_h, dtype=float)
    slot_h[0] = slot0_hours

    surplus_wh = np.clip(
        np.asarray(production_wh[:prod_end], dtype=float)
        - np.asarray(consumption_wh[:prod_end], dtype=float),
        0, None)
    surplus_hr_wh = surplus_wh * headroom
    feed_allow_wh = feed_in_limit_w * slot_h
    clip_wh = np.clip(surplus_hr_wh - feed_allow_wh, 0, None)

    clip_slots = np.nonzero(clip_wh > 0)[0]
    if len(clip_slots) == 0:
        return 0, -1

    first_clip = int(clip_slots[0])

    # -- Case A: before the clip window -> reservation cap ---------------- #
    # Free capacity minus the predicted clip energy is spread evenly over
    # the slots until the window starts. This prevents exportable energy
    # from displacing clip energy in the battery 1:1.
    if first_clip > 0:
        total_clip_wh = min(float(np.sum(clip_wh)), max_capacity_wh)
        allowed_wh = free_capacity_wh - total_clip_wh
        if allowed_wh <= 0:
            return 0, 0  # block PV charging, keep all capacity for the clip
        hours_before = slot0_hours + (first_clip - 1) * interval_h
        return 0, int(allowed_wh / hours_before)

    # -- Case B: inside a clip slot -> floor + capacity-preserving cap ---- #
    # The floor is computed from the headroom-adjusted clip: with a
    # greedy-charging inverter this only ever raises the allowed cap, it
    # never forces charging energy that does not actually exist.
    floor_w = clip_wh[0] / slot0_hours

    # The "everything fits, no cap needed" check uses the RAW (not
    # headroom-adjusted) surplus -- this is a physical check, not a safety
    # margin.
    total_surplus_wh = float(np.sum(surplus_wh))
    if total_surplus_wh <= free_capacity_wh:
        return int(floor_w), -1  # everything fits, no cap needed

    remaining_clip_wh = float(np.sum(clip_wh))
    extra_wh = max(0.0, free_capacity_wh - remaining_clip_wh)
    remaining_prod_h = float(np.sum(slot_h))
    # When clip energy alone exceeds free capacity (extra == 0) the cap
    # equals the floor: the battery absorbs ONLY otherwise-curtailed
    # energy, exportable surplus goes to the grid instead of displacing
    # clip energy.
    cap_w = int(floor_w + extra_wh / remaining_prod_h)
    return int(floor_w), cap_w


def merge_limits(floor_w, caps):
    """Merge the solar floor with a list of caps: ``final = max(floor, min(caps))``.

    ``caps`` entries: ``None`` or a negative value other than the sentinel
    means "no opinion" and is ignored; ``-1`` explicitly means "no cap";
    ``0`` blocks charging. Rationale (see the "Priorities between the rule
    flavors" section of docs/development/solar-limit-evaluation.md): caps
    optimize economics (shift charging in time), the floor prevents
    physical loss (curtailment) and therefore overrides every cap. An
    unlimited cap (``-1``) automatically satisfies any floor because the
    inverter then charges PV surplus greedily anyway.

    Returns:
        int: -1 (no limit), 0 (block), or a positive charge rate in W.
    """
    active = [c for c in caps if c is not None and c >= 0]
    if not active:
        return -1
    return max(int(floor_w), min(active))
