import { useEffect, useMemo, useState } from 'react'
import {
  getTissues,
  getExercises,
  updateExercise,
  type WkTissue,
  type WkExercise,
} from '../api'

// ── Types ──

type View = 'tissues' | 'exercises'

interface TissueNode extends WkTissue {
  children: TissueNode[]
  exercises: { exercise_id: number; exercise_name: string; role: string; loading_factor: number }[]
}

// ── Helpers ──

function buildTree(tissues: WkTissue[], exercises: WkExercise[]): TissueNode[] {
  const byId = new Map<number, TissueNode>()
  for (const t of tissues) {
    byId.set(t.id, { ...t, children: [], exercises: [] })
  }

  // Attach exercise mappings to tissues
  for (const ex of exercises) {
    for (const m of ex.tissues) {
      const node = byId.get(m.tissue_id)
      if (node) {
        node.exercises.push({
          exercise_id: ex.id,
          exercise_name: ex.name,
          role: m.role,
          loading_factor: m.loading_factor,
        })
      }
    }
  }

  // Build parent-child
  const roots: TissueNode[] = []
  for (const node of byId.values()) {
    if (node.parent_id !== null) {
      const parent = byId.get(node.parent_id)
      if (parent) {
        parent.children.push(node)
        continue
      }
    }
    roots.push(node)
  }

  // Sort children alphabetically
  const sortChildren = (nodes: TissueNode[]) => {
    nodes.sort((a, b) => a.display_name.localeCompare(b.display_name))
    for (const n of nodes) sortChildren(n.children)
  }
  sortChildren(roots)

  return roots
}

const TYPE_BADGE: Record<string, string> = {
  tissue_group: 'bg-blue-100 text-blue-700',
  muscle: 'bg-green-100 text-green-700',
  tendon: 'bg-orange-100 text-orange-700',
  joint: 'bg-red-100 text-red-700',
}

const ROLE_COLORS: Record<string, string> = {
  primary: 'bg-emerald-100 text-emerald-800',
  secondary: 'bg-sky-100 text-sky-800',
  stabilizer: 'bg-gray-100 text-gray-600',
}

const GROUP_COLORS: Record<string, string> = {
  upper_body: 'border-l-sky-400 bg-sky-50/50',
  lower_body: 'border-l-violet-400 bg-violet-50/50',
  core: 'border-l-amber-400 bg-amber-50/50',
}

// ── Components ──

function LoadingEditor({
  value,
  role,
  exerciseId,
  tissueId,
  exercise,
  onSave,
}: {
  value: number
  role: string
  exerciseId: number
  tissueId: number
  exercise: WkExercise
  onSave: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [loading, setLoading] = useState(value)
  const [editRole, setEditRole] = useState(role)
  const [saving, setSaving] = useState(false)

  if (!editing) {
    return (
      <button
        onClick={() => setEditing(true)}
        className="text-xs font-mono hover:bg-gray-100 px-1.5 py-0.5 rounded transition-colors"
        title="Click to edit"
      >
        {value.toFixed(2)}
      </button>
    )
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      // Build updated tissues list for this exercise
      const updatedTissues = exercise.tissues.map((t) =>
        t.tissue_id === tissueId
          ? { tissue_id: t.tissue_id, role: editRole, loading_factor: loading }
          : { tissue_id: t.tissue_id, role: t.role, loading_factor: t.loading_factor }
      )
      await updateExercise(exerciseId, { tissues: updatedTissues })
      onSave()
      setEditing(false)
    } catch (e) {
      console.error('Failed to save', e)
    } finally {
      setSaving(false)
    }
  }

  return (
    <span className="inline-flex items-center gap-1">
      <input
        type="number"
        min={0}
        max={1}
        step={0.05}
        value={loading}
        onChange={(e) => setLoading(parseFloat(e.target.value) || 0)}
        className="w-16 text-xs font-mono border border-gray-300 rounded px-1 py-0.5"
        autoFocus
      />
      <select
        value={editRole}
        onChange={(e) => setEditRole(e.target.value)}
        className="text-[10px] border border-gray-300 rounded px-1 py-0.5"
      >
        <option value="primary">primary</option>
        <option value="secondary">secondary</option>
        <option value="stabilizer">stabilizer</option>
      </select>
      <button
        onClick={handleSave}
        disabled={saving}
        className="text-[10px] bg-blue-500 text-white px-1.5 py-0.5 rounded hover:bg-blue-600 disabled:opacity-50"
      >
        {saving ? '...' : 'OK'}
      </button>
      <button
        onClick={() => { setEditing(false); setLoading(value); setEditRole(role) }}
        className="text-[10px] text-gray-500 hover:text-gray-700 px-1"
      >
        X
      </button>
    </span>
  )
}

function TissueTreeNode({
  node,
  depth,
  collapsed,
  toggleCollapse,
  exercises,
  onSave,
}: {
  node: TissueNode
  depth: number
  collapsed: Set<number>
  toggleCollapse: (id: number) => void
  exercises: WkExercise[]
  onSave: () => void
}) {
  const hasChildren = node.children.length > 0
  const isCollapsed = collapsed.has(node.id)
  const rootGroup = depth === 0 ? GROUP_COLORS[node.name] : undefined

  return (
    <>
      <div
        className={`border-b border-gray-100 hover:bg-gray-50/80 transition-colors ${rootGroup ? `border-l-4 ${rootGroup}` : ''}`}
        style={{ paddingLeft: `${depth * 20 + 12}px` }}
      >
        <div className="py-1.5 pr-3 flex items-start gap-2">
          {/* Expand/collapse */}
          <div className="w-4 flex-shrink-0 pt-0.5">
            {hasChildren && (
              <button
                onClick={() => toggleCollapse(node.id)}
                className="text-[10px] text-gray-400 hover:text-gray-600 font-mono w-4 h-4 flex items-center justify-center"
              >
                {isCollapsed ? '+' : '-'}
              </button>
            )}
          </div>

          {/* Names */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium text-gray-800">{node.display_name}</span>
              <span className="text-[10px] font-mono text-gray-400">{node.name}</span>
              <span className={`text-[10px] px-1.5 py-px rounded font-medium ${TYPE_BADGE[node.type] ?? 'bg-gray-100 text-gray-600'}`}>
                {node.type}
              </span>
              <span className="text-[10px] text-gray-400">{node.recovery_hours}h</span>
            </div>

            {/* Exercises that impact this tissue */}
            {node.exercises.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                {node.exercises.map((ex) => {
                  const fullExercise = exercises.find((e) => e.id === ex.exercise_id)
                  return (
                    <span key={ex.exercise_id} className="inline-flex items-center gap-1 text-[11px]">
                      <span className="text-gray-600">{ex.exercise_name}</span>
                      <span className={`px-1 py-px rounded text-[9px] font-medium ${ROLE_COLORS[ex.role] ?? ROLE_COLORS.stabilizer}`}>
                        {ex.role}
                      </span>
                      {fullExercise && (
                        <LoadingEditor
                          value={ex.loading_factor}
                          role={ex.role}
                          exerciseId={ex.exercise_id}
                          tissueId={node.id}
                          exercise={fullExercise}
                          onSave={onSave}
                        />
                      )}
                    </span>
                  )
                })}
              </div>
            )}
          </div>

          {/* Descendant count */}
          {hasChildren && (
            <span className="text-[10px] text-gray-400 flex-shrink-0 pt-0.5">
              {countDescendants(node)} tissues
            </span>
          )}
        </div>
      </div>

      {/* Children */}
      {hasChildren && !isCollapsed && (
        node.children.map((child) => (
          <TissueTreeNode
            key={child.id}
            node={child}
            depth={depth + 1}
            collapsed={collapsed}
            toggleCollapse={toggleCollapse}
            exercises={exercises}
            onSave={onSave}
          />
        ))
      )}
    </>
  )
}

function countDescendants(node: TissueNode): number {
  let count = 0
  for (const child of node.children) {
    count += 1 + countDescendants(child)
  }
  return count
}

// ── Exercise View ──

function ExerciseView({ exercises, onSave }: { exercises: WkExercise[]; onSave: () => void }) {
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    if (!search) return exercises
    const q = search.toLowerCase()
    return exercises.filter((e) => e.name.toLowerCase().includes(q))
  }, [exercises, search])

  return (
    <div>
      <input
        type="text"
        placeholder="Filter exercises..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="w-full max-w-sm border border-gray-300 rounded-lg px-3 py-1.5 text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-blue-400"
      />

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden divide-y divide-gray-100">
        {filtered.length === 0 && (
          <p className="text-sm text-gray-500 p-4">No exercises found.</p>
        )}
        {filtered.map((ex) => (
          <div key={ex.id} className="px-4 py-2.5 hover:bg-gray-50/80 transition-colors">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-gray-800">{ex.name}</span>
              {ex.equipment && (
                <span className="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-px rounded">
                  {ex.equipment}
                </span>
              )}
            </div>

            {ex.tissues.length > 0 ? (
              <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
                {ex.tissues.map((t) => (
                  <span key={t.tissue_id} className="inline-flex items-center gap-1 text-[11px]">
                    <span className="text-gray-600">{t.tissue_display_name}</span>
                    <span className="text-[10px] font-mono text-gray-400">({t.tissue_name})</span>
                    <span className={`px-1 py-px rounded text-[9px] font-medium ${ROLE_COLORS[t.role] ?? ROLE_COLORS.stabilizer}`}>
                      {t.role}
                    </span>
                    <LoadingEditor
                      value={t.loading_factor}
                      role={t.role}
                      exerciseId={ex.id}
                      tissueId={t.tissue_id}
                      exercise={ex}
                      onSave={onSave}
                    />
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-[11px] text-gray-400 mt-0.5 italic">No tissue mappings</p>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Main Page ──

export default function TissueAdminPage() {
  const [tissues, setTissues] = useState<WkTissue[]>([])
  const [exercises, setExercises] = useState<WkExercise[]>([])
  const [view, setView] = useState<View>('tissues')
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)

  const load = async () => {
    try {
      const [t, e] = await Promise.all([getTissues(), getExercises()])
      setTissues(t)
      setExercises(e)
    } catch (err) {
      console.error('Failed to load data', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const tree = useMemo(() => buildTree(tissues, exercises), [tissues, exercises])

  const toggleCollapse = (id: number) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  const collapseAll = () => {
    const ids = new Set<number>()
    const walk = (nodes: TissueNode[]) => {
      for (const n of nodes) {
        if (n.children.length > 0) ids.add(n.id)
        walk(n.children)
      }
    }
    walk(tree)
    setCollapsed(ids)
  }

  const expandAll = () => setCollapsed(new Set())

  // Filter tree nodes for tissue search
  const filteredTree = useMemo(() => {
    if (!search) return tree
    const q = search.toLowerCase()

    const filterNodes = (nodes: TissueNode[]): TissueNode[] => {
      const result: TissueNode[] = []
      for (const node of nodes) {
        const matchesSelf =
          node.name.toLowerCase().includes(q) ||
          node.display_name.toLowerCase().includes(q)
        const filteredChildren = filterNodes(node.children)
        if (matchesSelf || filteredChildren.length > 0) {
          result.push({ ...node, children: matchesSelf ? node.children : filteredChildren })
        }
      }
      return result
    }
    return filterNodes(tree)
  }, [tree, search])

  if (loading) {
    return <div className="text-sm text-gray-500 p-6">Loading...</div>
  }

  return (
    <div className="space-y-4 pb-8 overflow-y-auto h-full">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-lg font-semibold text-gray-800">Tissue & Exercise Admin</h1>
        <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-0.5">
          <button
            onClick={() => setView('tissues')}
            className={`text-xs px-3 py-1.5 rounded-md transition-colors font-medium ${
              view === 'tissues' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            Tissues ({tissues.length})
          </button>
          <button
            onClick={() => setView('exercises')}
            className={`text-xs px-3 py-1.5 rounded-md transition-colors font-medium ${
              view === 'exercises' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            Exercises ({exercises.length})
          </button>
        </div>
      </div>

      {/* Tissue View */}
      {view === 'tissues' && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <input
              type="text"
              placeholder="Filter tissues..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="flex-1 max-w-sm border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
            />
            <button onClick={expandAll} className="text-[11px] text-gray-500 hover:text-gray-700 px-2 py-1">
              Expand all
            </button>
            <button onClick={collapseAll} className="text-[11px] text-gray-500 hover:text-gray-700 px-2 py-1">
              Collapse all
            </button>
          </div>

          {/* Legend */}
          <div className="flex flex-wrap gap-3 mb-3 text-[10px]">
            {Object.entries(TYPE_BADGE).map(([type, cls]) => (
              <span key={type} className={`px-1.5 py-px rounded font-medium ${cls}`}>{type}</span>
            ))}
            <span className="text-gray-400 ml-2">|</span>
            {Object.entries(ROLE_COLORS).map(([role, cls]) => (
              <span key={role} className={`px-1.5 py-px rounded font-medium ${cls}`}>{role}</span>
            ))}
          </div>

          <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
            {filteredTree.map((node) => (
              <TissueTreeNode
                key={node.id}
                node={node}
                depth={0}
                collapsed={collapsed}
                toggleCollapse={toggleCollapse}
                exercises={exercises}
                onSave={load}
              />
            ))}
          </div>
        </div>
      )}

      {/* Exercise View */}
      {view === 'exercises' && (
        <ExerciseView exercises={exercises} onSave={load} />
      )}
    </div>
  )
}
