import type { Axis } from '../types'

/** A two-sided bar: the marker sits left or right of centre depending on the value (-100..100). */
export default function AxisBar({ axis }: { axis: Axis }) {
  const pos = ((axis.value + 100) / 200) * 100
  const lean = Math.abs(axis.value) < 15 ? 'balanced' : axis.value > 0 ? axis.high : axis.low
  return (
    <li className="axis" title={`${axis.name}: ${lean.toLowerCase()}`}>
      <span className="axis-low">{axis.low}</span>
      <span className="axis-track">
        <span className="axis-mid" />
        <span className="axis-marker" style={{ left: `${pos}%` }} />
      </span>
      <span className="axis-high">{axis.high}</span>
    </li>
  )
}
