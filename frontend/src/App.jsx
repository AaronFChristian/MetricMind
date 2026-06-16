import React, { useState } from 'react'
import { Brain, BookOpen, AlertTriangle } from 'lucide-react'
import CopilotView   from './views/CopilotView.jsx'
import CatalogView   from './views/CatalogView.jsx'
import AnomalyView   from './views/AnomalyView.jsx'

const VIEWS = [
  { id: 'copilot',  label: 'Copilot',         icon: Brain },
  { id: 'catalog',  label: 'Metrics Catalog',  icon: BookOpen },
  { id: 'anomalies',label: 'Anomaly Feed',     icon: AlertTriangle },
]

export default function App() {
  const [activeView, setActiveView] = useState('copilot')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>

      {/* ── Header ── */}
      <header style={{
        background:   'var(--bg-card)',
        borderBottom: '1px solid var(--border)',
        padding:      '0 24px',
        display:      'flex',
        alignItems:   'center',
        gap:          '24px',
        height:       '56px',
        position:     'sticky',
        top:          0,
        zIndex:       100,
      }}>
        {/* Logo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <div style={{
            width: '28px', height: '28px',
            background: 'var(--accent)',
            borderRadius: '8px',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <Brain size={16} color="white" />
          </div>
          <span style={{ fontWeight: 700, fontSize: '16px', color: 'var(--text)' }}>
            MetricMind
          </span>
          <span style={{
            fontSize: '10px', color: 'var(--accent)',
            background: 'var(--accent-glow)',
            padding: '2px 6px', borderRadius: '4px',
            fontWeight: 600, letterSpacing: '0.5px',
          }}>
            GOVERNED
          </span>
        </div>

        {/* Nav */}
        <nav style={{ display: 'flex', gap: '4px', marginLeft: '16px' }}>
          {VIEWS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveView(id)}
              style={{
                display:      'flex',
                alignItems:   'center',
                gap:          '6px',
                padding:      '6px 14px',
                borderRadius: '8px',
                border:       'none',
                cursor:       'pointer',
                fontSize:     '13px',
                fontWeight:   activeView === id ? 600 : 400,
                color:        activeView === id ? 'var(--accent)' : 'var(--text-muted)',
                background:   activeView === id ? 'var(--accent-glow)' : 'transparent',
                transition:   'all 0.15s',
              }}
            >
              <Icon size={14} />
              {label}
            </button>
          ))}
        </nav>

        {/* Right: status badge */}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '6px' }}>
          <div style={{
            width: '6px', height: '6px',
            borderRadius: '50%', background: 'var(--green)',
          }} />
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            6 certified metrics
          </span>
        </div>
      </header>

      {/* ── Main content ── */}
      <main style={{ flex: 1, padding: '24px', maxWidth: '1100px', margin: '0 auto', width: '100%' }}>
        {activeView === 'copilot'   && <CopilotView />}
        {activeView === 'catalog'   && <CatalogView />}
        {activeView === 'anomalies' && <AnomalyView />}
      </main>
    </div>
  )
}
