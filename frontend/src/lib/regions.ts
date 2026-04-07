const REGION_LABELS: Record<string, string> = {
  calves: 'Calves',
  tibs: 'Tibs',
  hamstrings: 'Hamstrings',
  quads: 'Quads',
  inner_leg_adductor: 'Inner Leg (Adductor)',
  outer_leg_abductor: 'Outer Leg (Abductor)',
  glutes: 'Glutes',
  core: 'Core',
  lower_back: 'Lower Back',
  upper_back: 'Upper Back',
  chest: 'Chest',
  triceps: 'Triceps',
  biceps: 'Biceps',
  forearms: 'Forearms',
  shoulders: 'Shoulders',
  neck: 'Neck',
  hands: 'Hands',
  feet: 'Feet',
}

export function regionLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return REGION_LABELS[value] ?? value.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase())
}
