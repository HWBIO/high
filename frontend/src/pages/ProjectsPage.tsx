import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { Position } from '../types'
import { notifyDataUpdated, onDataUpdated } from '../utils/dataEvents'
import { formatPositionLabel } from '../utils/positionLabel'
import { readPageCache, writePageCache } from '../utils/pageCache'

const POSITIONS_CACHE_KEY = 'projects:positions'

function positionMeta(position: Position) {
  const project = (() => {
    try {
      return JSON.parse(position.project_json || '{}')
    } catch {
      return {}
    }
  })() as Record<string, string>
  return {
    city: project.city || '未填写城市',
    salary: position.salary_range || '未填写薪资',
  }
}

export default function ProjectsPage() {
  const navigate = useNavigate()
  const [positions, setPositions] = useState<Position[]>(() => readPageCache<Position[]>(POSITIONS_CACHE_KEY, []))
  const [keyword, setKeyword] = useState('')
  const [companyFilter, setCompanyFilter] = useState('全部客户')
  const [error, setError] = useState('')

  const load = () => api.get<Position[]>('/api/positions').then((rows) => {
    writePageCache(POSITIONS_CACHE_KEY, rows)
    setPositions(rows)
  })

  useEffect(() => {
    load().catch((e) => setError(String((e as Error).message)))
    const onFocus = () => {
      load().catch((e) => setError(String((e as Error).message)))
    }
    window.addEventListener('focus', onFocus)
    const unsubscribe = onDataUpdated(['positions'], () => {
      load().catch((e) => setError(String((e as Error).message)))
    })
    return () => {
      window.removeEventListener('focus', onFocus)
      unsubscribe()
    }
  }, [])

  const filteredPositions = useMemo(() => {
    const q = keyword.trim().toLowerCase()
    return positions.filter((position) => {
      if (companyFilter !== '全部客户' && (position.company || '未指定客户') !== companyFilter) return false
      if (!q) return true
      return [
          position.title,
          position.company || '',
          position.seq_no || '',
          position.salary_range || '',
          position.project_json || '',
        ].join(' ').toLowerCase().includes(q)
    })
  }, [companyFilter, keyword, positions])

  const companyOptions = useMemo(
    () => Array.from(new Set(positions.map((position) => position.company || '未指定客户'))).sort((a, b) => a.localeCompare(b, 'zh-CN')),
    [positions],
  )

  const projectPositions = filteredPositions.filter((position) => position.is_project)
  const availablePositions = filteredPositions.filter((position) => !position.is_project)

  async function setProject(position: Position, isProject: boolean) {
    setError('')
    setPositions((rows) =>
      rows.map((row) => (row.id === position.id ? { ...row, is_project: isProject ? 1 : 0 } : row)),
    )
    try {
      await api.put(`/api/positions/${position.id}`, { is_project: isProject ? 1 : 0 })
      notifyDataUpdated('positions')
      notifyDataUpdated('projects')
    } catch (e) {
      setError(String((e as Error).message))
      await load()
    }
  }

  const renderPositionCard = (position: Position, action: 'add' | 'remove') => {
    const meta = positionMeta(position)
    return (
      <div className="project-position-card" key={position.id}>
        <div>
          <button className="link-button" onClick={() => navigate(`/positions/${position.id}`)}>
            {formatPositionLabel(position)}
          </button>
          <div className="project-tags">
            <span>{position.company || '未指定客户'}</span>
            <span>{meta.city}</span>
            <span>{meta.salary}</span>
          </div>
        </div>
        <button
          className={action === 'add' ? 'small' : 'secondary small'}
          onClick={() => setProject(position, action === 'add')}
        >
          {action === 'add' ? '加入项目' : '移出项目'}
        </button>
      </div>
    )
  }

  return (
    <div>
      <div className="folder-page-title">
        <h2>项目管理</h2>
      </div>
      <div className="card">
        <div className="project-filter-row">
          <select
            className="project-company-filter"
            value={companyFilter}
            onChange={(e) => setCompanyFilter(e.target.value)}
          >
            <option value="全部客户">全部客户</option>
            {companyOptions.map((company) => (
              <option key={company} value={company}>{company}</option>
            ))}
          </select>
          <div className="project-search-box">
            <input
              type="text"
              className="project-search-input"
              placeholder="搜索项目/岗位/客户/城市/薪资"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
            {keyword && (
              <button className="secondary" onClick={() => setKeyword('')}>
                清除
              </button>
            )}
          </div>
        </div>
        {error && <p className="error">{error}</p>}
      </div>
      <div className="project-board">
        <section className="project-drop-zone">
          <div className="project-panel-head">
            <h3>重点项目</h3>
            <span className="muted">{projectPositions.length} 个</span>
          </div>
          {projectPositions.map((position) => renderPositionCard(position, 'remove'))}
          {projectPositions.length === 0 && (
            <div className="empty-drop-zone">从右侧岗位池选择需要重点匹配的项目</div>
          )}
        </section>
        <section className="project-pool">
          <div className="project-panel-head">
            <h3>全部岗位</h3>
            <span className="muted">{availablePositions.length} 个</span>
          </div>
          {availablePositions.map((position) => renderPositionCard(position, 'add'))}
          {availablePositions.length === 0 && <p className="muted">暂无可加入的岗位。</p>}
        </section>
      </div>
    </div>
  )
}
