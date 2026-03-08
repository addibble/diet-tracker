import { type CSSProperties, type ReactNode } from 'react'

interface ScrollablePageProps {
  children: ReactNode
  className?: string
}

const pageHeightStyle: CSSProperties = {
  height: 'calc(100dvh - var(--safe-top) - var(--safe-bottom) - 4.25rem)',
}

export default function ScrollablePage({ children, className = '' }: ScrollablePageProps) {
  return (
    <div className="flex flex-col min-h-0" style={pageHeightStyle}>
      <div className={`flex-1 min-h-0 overflow-y-auto pr-1 ${className}`}>{children}</div>
    </div>
  )
}
