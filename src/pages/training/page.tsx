import { AppBar, Sizer } from '@/components'
import { Link } from 'react-router'

export default function TrainingPage() {
  const links = [
    { label: 'Speed', url: '/training/speed' },
    { label: 'Infinite', url: '/training/phrases' },
  ]
  return (
    <div className="flex min-h-screen flex-col bg-paper bg-amber-50/70">
      <AppBar />
      <Sizer height={48} />
      <div className="flex h-full grow content-center justify-center gap-5 py-6">
        {links.map(({ label, url }) => (
          <Link to={url} key={url} className="text-gray-900 no-underline">
            <div className="flex h-[200px] w-[200px] items-center justify-center rounded-lg border border-amber-200 bg-white shadow-sm transition-colors hover:bg-amber-50">
              {label}
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
