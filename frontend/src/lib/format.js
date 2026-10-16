export const kwh = (wh) => (wh == null ? '—' : `${(wh / 1000).toFixed(2)} kWh`)

export const eur = (v, digits = 3) => (v == null ? '—' : `${v.toFixed(digits)} €`)

export const pct = (v, digits = 1) => (v == null ? '—' : `${v.toFixed(digits)} %`)

export const clock = (ts) =>
  ts ? new Date(ts).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '—'

export function ago(ts, now) {
  if (!ts) return ''
  const s = Math.max(0, Math.round((now - ts) / 1000))
  if (s < 60) return `${s} s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} h ${m % 60} min ago`
  return `${Math.floor(h / 24)} d ${h % 24} h ago`
}
