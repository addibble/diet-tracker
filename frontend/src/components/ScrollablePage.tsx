import { type ReactNode } from 'react'

interface ScrollablePageProps {
  children: ReactNode
  className?: string
}

export default function ScrollablePage({ children, className = '' }: ScrollablePageProps) {
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className={`flex-1 min-h-0 overflow-y-auto pr-1 ${className}`}>{children}</div>
    </div>
  )
}
