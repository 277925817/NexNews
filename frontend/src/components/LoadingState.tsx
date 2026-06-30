type LoadingStateProps = {
  count?: number
}

export function LoadingState({ count = 5 }: LoadingStateProps) {
  return (
    <div className="loading-state" aria-busy="true" aria-label="新闻加载中">
      {Array.from({ length: count }).map((_, index) => (
        <div className="loading-state__row" key={index}>
          <span />
          <strong />
          <em />
        </div>
      ))}
    </div>
  )
}
