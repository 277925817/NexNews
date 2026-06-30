import type { CSSProperties } from 'react'

const SOURCE_COLORS = ['#7DD3FC', '#34D399', '#FBBF24', '#F87171', '#A78BFA', '#F472B6']

function getSourceColor(sourceName: string): string {
  const seed = Array.from(sourceName).reduce(
    (total, character) => (total * 31 + character.charCodeAt(0)) % 9973,
    7,
  )
  return SOURCE_COLORS[seed % SOURCE_COLORS.length]
}

type SourceMarkerProps = {
  sourceName: string
}

export function SourceMarker({ sourceName }: SourceMarkerProps) {
  return (
    <span
      className="source-marker"
      style={{ '--source-color': getSourceColor(sourceName) } as CSSProperties}
    >
      <span className="source-marker__dot" aria-hidden="true" />
      <span>{sourceName}</span>
    </span>
  )
}
