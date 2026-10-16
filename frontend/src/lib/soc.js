/**
 * State of the 100 cells in the SOC grid.
 *
 * Pure on purpose: this is the one piece of the dashboard with real logic in
 * it - five combinable states, two of them with off-by-one traps - and it is
 * worth testing against the table in docs/development/dashboard-spec.md
 * rather than against a rendered component.
 */

/** Rows for the grid, top row first. Cell 0 is bottom-left. */
export function socRows(state) {
  const cells = socCells(state)
  return Array.from({ length: 10 }, (_, i) => {
    const row = 9 - i
    return {
      start: row * 10,
      cells: cells.slice(row * 10, row * 10 + 10),
    }
  })
}

/**
 * The 100 cells, index 0..99, each standing for one percent.
 *
 * Cell p covers the interval (p-1, p].
 */
export function socCells({
  soc,
  minSoc = null,
  maxSoc = null,
  allowDischargeSoc = null,
  capacity = 0,
  reservedCapped = 0,
}) {
  // The cell holding the SoC blinks: 87.0 % -> cell 87, 87.1 % -> cell 88.
  // At 100 % there is no cell 100, so nothing blinks and everything is lit -
  // intentional, because balancing takes the step from 99 to 100 % almost
  // immediately anyway.
  const current = soc == null ? -1 : Math.ceil(soc)
  // An unknown limit is not a limit of zero: before the inverter has been
  // read, min_soc is null, and treating that as 0 marked cell 0 as
  // "locked (<= MIN)" when no limit is known at all.
  const floor = minSoc ?? 0
  const reservedTop = floor + (capacity ? (reservedCapped / capacity) * 100 : 0)

  return Array.from({ length: 100 }, (_, p) => ({
    p,
    on: p < current,
    partial: p === current,
    locked: (minSoc != null && p <= minSoc) ||
      (maxSoc != null && maxSoc < 100 && p > maxSoc),
    // Hatched as soon as the reservation reaches into the cell, including the
    // blinking one. Never above always_allow_discharge: nothing is held back
    // there.
    hatched: p > floor && p - 1 < reservedTop,
    discharge: allowDischargeSoc != null && p > allowDischargeSoc,
  }))
}
