import React, { useState, useRef, useEffect } from 'react'
import { Send, Loader, ChevronDown, ChevronUp, Zap, ArrowRight } from 'lucide-react'
import {
  BarChart, Bar, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts'

// ── API call ──────────────────────────────────────────────────────────────────
async function askQuestion(question) {
  const res = await fetch('/api/query', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ question }),
  })
  if (!res.ok) {
    const err = await res.json()
    throw new Error(err.detail || 'API error')
  }
  return res.json()
}

// ── Confidence badge ──────────────────────────────────────────────────────────
function ConfidenceBadge({ level }) {
  const styles = {
    high:   { bg: 'rgba(76,175,125,0.15)', color: '#4caf7d', label: '● High confidence' },
    medium: { bg: 'rgba(245,166,35,0.15)', color: '#f5a623', label: '● Medium confidence' },
    low:    { bg: 'rgba(245,99,66,0.15)',  color: '#f56342', label: '● Low confidence'  },
  }
  const s = styles[level] || styles.low
  return (
    <span style={{
      fontSize: '11px', fontWeight: 600,
      background: s.bg, color: s.color,
      padding: '3px 8px', borderRadius: '12px',
    }}>
      {s.label}
    </span>
  )
}

// ── Chart renderer ────────────────────────────────────────────────────────────
function ResultChart({ data }) {
  if (!data || data.length === 0) return null

  const keys    = Object.keys(data[0])
  const dateKey = keys.find(k => k.includes('date') || k.includes('month') || k.includes('week'))
  const numKeys = keys.filter(k => {
    const v = data[0][k]
    return typeof v === 'number' && !k.includes('id')
  })

  if (!numKeys.length) return null

  const valueKey = numKeys[0]
  const labelKey = dateKey || keys[0]

  // Format label for display
  const formatted = data.slice(0, 30).map(row => ({
    ...row,
    _label: String(row[labelKey] || '').slice(0, 10),
  }))

  const isTimeSeries = !!dateKey

  return (
    <div style={{ marginTop: '16px', height: '200px' }}>
      <ResponsiveContainer width="100%" height="100%">
        {isTimeSeries ? (
          <LineChart data={formatted}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="_label" tick={{ fontSize: 10, fill: 'var(--text-muted)' }} />
            <YAxis tick={{ fontSize: 10, fill: 'var(--text-muted)' }} />
            <Tooltip
              contentStyle={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: '8px' }}
              labelStyle={{ color: 'var(--text-muted)' }}
            />
            <Line type="monotone" dataKey={valueKey} stroke="var(--accent)" strokeWidth={2} dot={false} />
          </LineChart>
        ) : (
          <BarChart data={formatted}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="_label" tick={{ fontSize: 10, fill: 'var(--text-muted)' }} />
            <YAxis tick={{ fontSize: 10, fill: 'var(--text-muted)' }} />
            <Tooltip
              contentStyle={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: '8px' }}
            />
            <Bar dataKey={valueKey} fill="var(--accent)" radius={[4, 4, 0, 0]} />
          </BarChart>
        )}
      </ResponsiveContainer>
    </div>
  )
}

// ── Result card ───────────────────────────────────────────────────────────────
function ResultCard({ item, onFollowup }) {
  const [showSQL, setShowSQL] = useState(false)

  const isRejected = !item.guardrail_passed || item.intent === 'out_of_scope'

  return (
    <div style={{
      background:   'var(--bg-card)',
      border:       '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding:      '20px',
      marginBottom: '16px',
    }}>
      {/* Question */}
      <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '10px' }}>
        Q: {item.question}
      </div>

      {/* Answer */}
      <div style={{ fontSize: '15px', color: 'var(--text)', lineHeight: 1.7, marginBottom: '12px' }}>
        {item.answer}
      </div>

      {/* Meta row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
        <ConfidenceBadge level={item.confidence} />

        {item.cache_hit && (
          <span style={{ fontSize: '11px', color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: '3px' }}>
            <Zap size={11} /> cached
          </span>
        )}

        {item.row_count != null && (
          <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
            {item.row_count} rows · {item.execution_time_ms?.toFixed(0)}ms
          </span>
        )}

        <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
          ${item.estimated_cost?.toFixed(4)} · {item.tokens_used} tokens
        </span>

        {/* Show SQL toggle */}
        {item.sql && (
          <button
            onClick={() => setShowSQL(v => !v)}
            style={{
              marginLeft:   'auto',
              background:   'transparent',
              border:       '1px solid var(--border)',
              borderRadius: '6px',
              color:        'var(--text-muted)',
              fontSize:     '11px',
              padding:      '3px 8px',
              cursor:       'pointer',
              display:      'flex',
              alignItems:   'center',
              gap:          '4px',
            }}
          >
            {showSQL ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
            {showSQL ? 'Hide SQL' : 'Show SQL'}
          </button>
        )}
      </div>

      {/* SQL block */}
      {showSQL && item.sql && (
        <pre style={{
          marginTop:    '12px',
          background:   '#0d0f1a',
          border:       '1px solid var(--border)',
          borderRadius: '8px',
          padding:      '12px',
          color:        '#a5b4fc',
          fontSize:     '12px',
          overflowX:    'auto',
          whiteSpace:   'pre-wrap',
        }}>
          {item.sql}
        </pre>
      )}

      {/* Chart */}
      {item.result_data && <ResultChart data={item.result_data} />}

      {/* Follow-up suggestion */}
      {item.suggested_followup && !isRejected && (
        <button
          onClick={() => onFollowup(item.suggested_followup)}
          style={{
            marginTop:    '14px',
            background:   'var(--accent-glow)',
            border:       '1px solid var(--border)',
            borderRadius: '8px',
            color:        'var(--accent)',
            fontSize:     '12px',
            padding:      '7px 12px',
            cursor:       'pointer',
            display:      'flex',
            alignItems:   'center',
            gap:          '6px',
            width:        '100%',
            textAlign:    'left',
          }}
        >
          <ArrowRight size={12} />
          {item.suggested_followup}
        </button>
      )}
    </div>
  )
}

// ── Example questions ─────────────────────────────────────────────────────────
const EXAMPLES = [
  "What was DAU last month by country?",
  "Show me MRR trend for 2023",
  "What is 30-day retention for Pro plan?",
  "Show churn rate by plan",
  "What is LTV to CAC ratio for enterprise?",
]

// ── Main Copilot View ─────────────────────────────────────────────────────────
export default function CopilotView() {
  const [question, setQuestion]   = useState('')
  const [history,  setHistory]    = useState([])
  const [loading,  setLoading]    = useState(false)
  const [error,    setError]      = useState(null)
  const inputRef                  = useRef(null)
  const bottomRef                 = useRef(null)

  // Auto-scroll to latest result
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history])

  async function handleSubmit(q) {
    const text = (q || question).trim()
    if (!text || loading) return

    setQuestion('')
    setLoading(true)
    setError(null)

    try {
      const result = await askQuestion(text)
      setHistory(h => [...h, result])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
      inputRef.current?.focus()
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div>
      {/* Hero — shown only when no history */}
      {history.length === 0 && !loading && (
        <div style={{ textAlign: 'center', padding: '48px 0 32px' }}>
          <div style={{
            width: '56px', height: '56px',
            background: 'var(--accent)',
            borderRadius: '16px',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            margin: '0 auto 16px',
          }}>
            <Send size={24} color="white" />
          </div>
          <h1 style={{ fontSize: '24px', fontWeight: 700, marginBottom: '8px' }}>
            Ask your metrics anything
          </h1>
          <p style={{ color: 'var(--text-muted)', maxWidth: '480px', margin: '0 auto 32px' }}>
            Governed text-to-SQL copilot — every answer is traceable to a certified dbt model.
            The LLM cannot invent metrics that don't exist.
          </p>

          {/* Example questions */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center' }}>
            {EXAMPLES.map(ex => (
              <button
                key={ex}
                onClick={() => handleSubmit(ex)}
                style={{
                  background:   'var(--bg-card)',
                  border:       '1px solid var(--border)',
                  borderRadius: '20px',
                  color:        'var(--text-muted)',
                  fontSize:     '12px',
                  padding:      '6px 14px',
                  cursor:       'pointer',
                }}
              >
                {ex}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Query history */}
      {history.map((item, i) => (
        <ResultCard
          key={i}
          item={item}
          onFollowup={q => { setQuestion(q); handleSubmit(q) }}
        />
      ))}

      {/* Loading state */}
      {loading && (
        <div style={{
          background:   'var(--bg-card)',
          border:       '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          padding:      '24px',
          marginBottom: '16px',
          display:      'flex',
          alignItems:   'center',
          gap:          '12px',
          color:        'var(--text-muted)',
        }}>
          <Loader size={16} style={{ animation: 'spin 1s linear infinite' }} />
          Running through 5-node pipeline...
          <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>
        </div>
      )}

      {/* Error */}
      {error && (
        <div style={{
          background: 'rgba(245,99,66,0.1)',
          border:     '1px solid var(--red)',
          borderRadius: 'var(--radius)',
          padding:    '12px 16px',
          color:      'var(--red)',
          marginBottom: '16px',
          fontSize:   '13px',
        }}>
          Error: {error}
        </div>
      )}

      <div ref={bottomRef} />

      {/* ── Input bar ── */}
      <div style={{
        position:     'sticky',
        bottom:       '24px',
        background:   'var(--bg-card)',
        border:       '1px solid var(--border)',
        borderRadius: '16px',
        padding:      '12px 16px',
        display:      'flex',
        gap:          '10px',
        alignItems:   'center',
        boxShadow:    'var(--shadow)',
        marginTop:    '16px',
      }}>
        <input
          ref={inputRef}
          value={question}
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your metrics... (e.g. 'What was DAU last month by country?')"
          disabled={loading}
          style={{
            flex:       1,
            background: 'transparent',
            border:     'none',
            outline:    'none',
            color:      'var(--text)',
            fontSize:   '14px',
          }}
        />
        <button
          onClick={() => handleSubmit()}
          disabled={!question.trim() || loading}
          style={{
            background:   question.trim() && !loading ? 'var(--accent)' : 'var(--border)',
            border:       'none',
            borderRadius: '10px',
            width:        '36px',
            height:       '36px',
            display:      'flex',
            alignItems:   'center',
            justifyContent: 'center',
            cursor:       question.trim() && !loading ? 'pointer' : 'not-allowed',
            transition:   'background 0.15s',
            flexShrink:   0,
          }}
        >
          <Send size={15} color="white" />
        </button>
      </div>
    </div>
  )
}
