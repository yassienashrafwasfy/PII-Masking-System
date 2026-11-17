import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'
import './console.css'
// Last, so their reduced-motion blocks can override the animations they
// introduce without needing a specificity bump over the two above. themes.css
// follows theatrics.css because the vault and constellation themes restyle a
// few surfaces the colosseum layer also touches, and the later file should win.
import './theatrics.css'
import './themes.css'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
