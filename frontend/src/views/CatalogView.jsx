import React, { useState, useEffect } from 'react'
import { BookOpen, Tag, Clock } from 'lucide-react'

export default function CatalogView() {
  const [metrics, setMetrics] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/metrics')
      .then(r => r.json())
      .then(d => { setMetrics(d.metrics || []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const COLORS = ['#7c6af7','#4caf7d','#f5a623','#f56342','#56b6e9','#e879a0']

  if (loading) return (
    <div style={{ textAlign: 'center', padding: '64px', color: 'var(--text-muted)' }}>
      Loading catalog...
    </div>
  )

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: '28px' }}>
        <h2 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '6px' }}>
          Certified Metrics Catalog
        </h2>
        <p style={{ color: 'var(--text-muted)' }}>
          {metrics.length} governed metrics — these are the only metrics the AI agent can query.
          Every answer is traceable to one of these definitions.
        </p>
      </div>

      {/* Grid */}
      <div style={{
        display:             'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
        gap:                 '16px',
      }}>
        {metrics.map((m, i) => (
          <div key={m.name} style={{
            background:   'var(--bg-card)',
            border:       '1px solid var(--border)',
            borderRadius: 'var(--radius)',
            padding:      '20px',
            borderTop:    `3px solid ${COLORS[i % COLORS.length]}`,
          }}>
            {/* Name */}
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', marginBottom: '10px' }}>
              <div style={{
                width: '32px', height: '32px', flexShrink: 0,
                background: `${COLORS[i % COLORS.length]}20`,
                borderRadius: '8px',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <BookOpen size={14} color={COLORS[i % COLORS.length]} />
              </div>
              <div>
                <div style={{ fontWeight: 700, fontSize: '14px' }}>{m.label || m.name}</div>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                  {m.name}
                </div>
              </div>
            </div>

            {/* Description */}
            <p style={{ fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.6, marginBottom: '14px' }}>
              {(m.description || '').slice(0, 160)}
            </p>

            {/* Source table */}
            <div style={{
              display:      'flex',
              alignItems:   'center',
              gap:          '6px',
              fontSize:     '11px',
              color:        'var(--text-muted)',
              background:   'var(--bg)',
              padding:      '6px 10px',
              borderRadius: '6px',
              marginBottom: '14px',
              fontFamily:   'monospace',
            }}>
              <Tag size={10} />
              {m.source_table}
            </div>

            {/* Dimensions */}
            {m.allowed_dimensions?.length > 0 && (
              <div style={{ marginBottom: '12px' }}>
                <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Dimensions
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                  {m.allowed_dimensions.map(d => (
                    <span key={d} style={{
                      fontSize: '11px', color: COLORS[i % COLORS.length],
                      background: `${COLORS[i % COLORS.length]}15`,
                      padding: '2px 8px', borderRadius: '12px',
                    }}>
                      {d}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Example questions */}
            {m.example_questions?.length > 0 && (
              <div>
                <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Example questions
                </div>
                {m.example_questions.slice(0, 2).map((q, j) => (
                  <div key={j} style={{
                    fontSize: '11px', color: 'var(--text-muted)',
                    padding: '4px 0',
                    borderBottom: j < 1 ? '1px solid var(--border)' : 'none',
                  }}>
                    "{q}"
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
