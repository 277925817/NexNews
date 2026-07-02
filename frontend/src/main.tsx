import React from 'react'
import ReactDOM from 'react-dom/client'

import { newsAPIClient } from './api/news'
import { sourceAPIClient } from './api/sources'
import { ArticleView } from './pages/ArticleView'
import { HomePage } from './pages/HomePage'
import { SourcesPage } from './pages/SourcesPage'
import './styles/app.css'
import './styles/article.css'
import './styles/sources.css'

function getArticleId(pathname: string) {
  const match = pathname.match(/^\/news\/([^/]+)$/)
  return match ? decodeURIComponent(match[1]) : null
}

function renderCurrentRoute() {
  const articleId = getArticleId(window.location.pathname)

  if (articleId) {
    return <ArticleView client={newsAPIClient} newsId={articleId} />
  }

  if (window.location.pathname === '/sources') {
    return <SourcesPage client={sourceAPIClient} onRefresh={newsAPIClient.refreshHome} />
  }

  return <HomePage client={newsAPIClient} />
}

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    {renderCurrentRoute()}
  </React.StrictMode>,
)
