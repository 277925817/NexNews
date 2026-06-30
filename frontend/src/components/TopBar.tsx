type TopBarProps = {
  isRefreshing: boolean
  onRefresh: () => void
}

export function TopBar({ isRefreshing, onRefresh }: TopBarProps) {
  return (
    <header className="top-bar">
      <a className="top-bar__brand" href="/">
        NexNews
      </a>
      <nav className="top-bar__actions" aria-label="主导航">
        <button className="top-bar__button" type="button" disabled={isRefreshing} onClick={onRefresh}>
          {isRefreshing ? '刷新中' : '刷新'}
        </button>
        <a className="top-bar__button" href="/sources">
          信源
        </a>
      </nav>
    </header>
  )
}
