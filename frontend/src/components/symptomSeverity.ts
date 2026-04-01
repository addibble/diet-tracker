export type SymptomSeverityLevel = 0 | 1 | 2 | 3

export const SYMPTOM_SEVERITY_TO_DB = [0, 2, 5, 7] as const

export const SYMPTOM_SEVERITY_OPTIONS = [
  {
    label: 'None',
    range: '0/10',
    description: 'No symptom provocation.',
  },
  {
    label: 'Mild',
    range: '2/10',
    description: 'Low, easily controlled symptoms.',
  },
  {
    label: 'Moderate',
    range: '5/10',
    description: 'Clear symptoms that may be acceptable only in guarded tendon loading.',
  },
  {
    label: 'Severe',
    range: '7/10',
    description: 'High or limiting symptoms that are generally a back-off signal.',
  },
] as const

export const symptomDbToSeverity = (value: number): SymptomSeverityLevel =>
  value > 6 ? 3 : value > 3 ? 2 : value > 0 ? 1 : 0

export const symptomSeverityToDb = (level: SymptomSeverityLevel): number =>
  SYMPTOM_SEVERITY_TO_DB[level]
