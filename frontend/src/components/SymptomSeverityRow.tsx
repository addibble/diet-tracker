import {
  SYMPTOM_SEVERITY_OPTIONS,
  type SymptomSeverityLevel,
} from './symptomSeverity'

const SYMPTOM_SEVERITY_COLORS = [
  'bg-emerald-100 text-emerald-700 border-emerald-300',
  'bg-yellow-100 text-yellow-700 border-yellow-300',
  'bg-amber-100 text-amber-700 border-amber-300',
  'bg-red-100 text-red-700 border-red-300',
] as const

export function SymptomSeverityRow({
  label,
  value,
  onChange,
  showDescription = true,
}: {
  label: string
  value: SymptomSeverityLevel
  onChange: (value: SymptomSeverityLevel) => void
  showDescription?: boolean
}) {
  const selected = SYMPTOM_SEVERITY_OPTIONS[value]

  return (
    <div className="space-y-1.5">
      <span className="text-xs font-medium text-gray-600">{label}</span>
      <div className="grid grid-cols-4 gap-1.5">
        {SYMPTOM_SEVERITY_OPTIONS.map((option, index) => {
          const level = index as SymptomSeverityLevel
          return (
            <button
              key={option.label}
              type="button"
              onClick={() => onChange(level)}
              title={`${option.range} - ${option.description}`}
              className={`rounded-lg border px-2 py-1.5 text-xs font-medium transition-all ${
                value === level
                  ? SYMPTOM_SEVERITY_COLORS[level]
                  : 'bg-white border-gray-200 text-gray-400 hover:border-gray-300'
              }`}
            >
              {option.label}
            </button>
          )
        })}
      </div>
      {showDescription && (
        <p className="text-[10px] text-gray-500">
          {selected.label} ({selected.range}) - {selected.description}
        </p>
      )}
    </div>
  )
}

export default SymptomSeverityRow
