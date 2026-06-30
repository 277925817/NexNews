type ScoreBadgeProps = {
  score: number
}

export function ScoreBadge({ score }: ScoreBadgeProps) {
  return (
    <span className="score-badge" aria-label={`评分 ${score}`}>
      {score}
    </span>
  )
}
