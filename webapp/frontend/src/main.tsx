import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// Apply persisted theme before first paint to avoid a flash. Default is dark.
try {
  const saved = localStorage.getItem('aqr_theme')
  if (saved === 'light') document.body.classList.add('light')
  else document.body.classList.add('dark')
} catch { document.body.classList.add('dark') }

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
