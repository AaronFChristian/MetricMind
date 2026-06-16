import React, { useState, useEffect } from 'react'
import { AlertTriangle, CheckCircle, TrendingDown, TrendingUp, Clock } from 'lucide-react'

export default function AnomalyView() {
  const [anomalies, setAnomalies] = useState([])
  const [loading,   setLoading]   = useState(true)
  const [message,   setMessage]   = useState(null)

  useEffect(() => {
    fetch('/api/anomalies')
      .then(r => r.json())
      .then(d => { setAnomalies(d.anomalies || []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  async function handleApprove(id) {
    try {
      await fetch(`/api/anomalies/${id}/approve`, { method: 'POST' })
      setAnomalies(a => a.map(x => x.id === id ? { ...x, commentary_approved: true } : x))
      setMessage('Commentary approved ✓')
      setTimeout(() => setMessage(null), 3000)
    } catch(e) {
      setMessage('Error approving')
    }
  }

  if (loading) return (
    <div style={{ textAlign: 'center', padding: '64px', color: 'var(--text-muted)' }}>
      Loading anomaly feed...
    </div>
  )

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: '28px' }}>
        <h2 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '6px' }}>
          Anomaly Feed
        </h2>
        <p style={{ color: 'var(--text-muted)' }}>
          Statistical anomalies detected in your metrics. LLM-generated commentary requires
          human approval before publishing — this is the human-in-the-loop checkpoint.
        </p>
      </div>

      {/* Toast */}
      {message && (
        <div style={{
          background: 'rgba(76,175,125,0.15)', border: '1px solid var(--green)',
          borderRadius: '8px', padding: '10px 16px', color: 'var(--green)',
          marginBottom: '16px', fontSize: '13px',
        }}>
          {message}
        </div>
      )}

      {/* Empty state */}
      {anomalies.length === 0 && (
        <div style={{
          background: 'var(--bg-card)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius)', padding: '48px',
          textAlign: 'center', color: 'var(--text-muted)',
        }}>
          <AlertTriangle size={32} style={{ marginBottom: '12px', opacity: 0.4 }} />
          <p style={{ marginBottom: '8px' }}>No anomalies detected yet.</p>
          <code style={{ fontSize: '12px', color: 'var(--accent)' }}>
            python anomaly/detector.py
          </code>
        </div>
      )}

      {/* Anomaly cards */}
      {anomalies.map((a, i) => (
        <div key={i} style={{
          background:   'var(--bg-card)',
          border:       `1px solid ${a.direction === 'drop' ? 'rgba(245,99,66,0.3)' : 'rgba(76,175,125,0.3)'}`,
          borderRadius: 'var(--radius)',
          padding:      '20px',
          marginBottom: '12px',
        }}>
          {/* Top row */}
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '12px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              {a.direction === 'drop'
                ? <TrendingDown size={18} color="var(--red)" />
                : <TrendingUp   size={18} color="var(--green)" />
              }
              <div>
                <div style={{ fontWeight: 600, fontSize: '14px' }}>
                  {a.metric_name?.replace(/_/g, ' ')}
                </div>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                  <Clock size={10} /> {a.anomaly_date} · {a.method}
                </div>
              </div>
            </div>

            {/* Deviation badge */}
            <span style={{
              fontSize:   '13px',
              fontWeight: 700,
              color:      a.direction === 'drop' ? 'var(--red)' : 'var(--green)',
              background: a.direction === 'drop' ? 'rgba(245,99,66,0.1)' : 'rgba(76,175,125,0.1)',
              padding:    '4px 10px',
              borderRadius: '8px',
            }}>
              {a.deviation_pct > 0 ? '+' : ''}{a.deviation_pct?.toFixed(1)}%
            </span>
          </div>

          {/* Values */}
          <div style={{
            display: 'grid', gridTemplateColumns: '1fr 1fr',
            gap: '8px', marginBottom: '14px',
          }}>
            {[
              { label: 'Actual',   value: a.actual_value?.toLocaleString() },
              { label: 'Expected', value: a.expected_value?.toLocaleString() },
            ].map(({ label, value }) => (
              <div key={label} style={{
                background: 'var(--bg)', borderRadius: '8px', padding: '10px 12px',
              }}>
                <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginBottom: '2px', textTransform: 'uppercase' }}>
                  {label}
                </div>
                <div style={{ fontSize: '16px', fontWeight: 700 }}>{value}</div>
              </div>
            ))}
          </div>

          {/* LLM Commentary */}
          <div style={{
            background: 'var(--bg)', borderRadius: '8px', padding: '12px',
            fontSize: '13px', lineHeight: 1.6, color: 'var(--text)', marginBottom: '14px',
            borderLeft: '3px solid var(--accent)',
          }}>
            {a.commentary}
          </div>

          {/* HITL Approve button */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            {a.commentary_approved ? (
              <span style={{
                display: 'flex', alignItems: 'center', gap: '6px',
                color: 'var(--green)', fontSize: '12px',
              }}>
                <CheckCircle size={14} /> Commentary approved
              </span>
            ) : (
              <>
                <span style={{ fontSize: '11px', color: 'var(--yellow)' }}>
                  ⚠ Requires human review before publishing
                </span>
                <button
                  onClick={() => handleApprove(a.id)}
                  style={{
                    marginLeft:   'auto',
                    background:   'rgba(76,175,125,0.15)',
                    border:       '1px solid var(--green)',
                    borderRadius: '8px',
                    color:        'var(--green)',
                    fontSize:     '12px',
                    padding:      '6px 14px',
                    cursor:       'pointer',
                    fontWeight:   600,
                  }}
                >
                  Approve Commentary
                </button>
              </>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
