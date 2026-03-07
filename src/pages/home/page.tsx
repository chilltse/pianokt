import { AppBar, MarketingFooter, Sizer } from '@/components'
import { useOptionalAuth } from '@/features/auth'
import React from 'react'
import { Link } from 'react-router'
import { FeaturedSongsPreview } from './FeaturedSongsPreview'
import { Leaderboard } from './Leaderboard'

export default function Home() {
  const auth = useOptionalAuth()
  const user = auth?.user ?? null
  return (
    <>
      <div className="bg-paper bg-amber-50/70 relative flex min-h-screen w-full flex-col text-gray-700">
        <AppBar />
        <div className="bg-paper bg-amber-50/70 border-b border-amber-100">
          <div className="mx-auto w-full max-w-(--breakpoint-lg) px-6 py-10">
            <div className="grid items-center gap-8 lg:grid-cols-[1.1fr_0.9fr]">
              <div className="flex flex-col gap-4 text-center lg:text-left">
                <h1 className="text-responsive-xxl font-bold text-gray-900">AI powered piano learning journey</h1>
                <h3 className="text-responsive-xl text-gray-600">
                  Plug in your MIDI keyboard and see your progress <br />Compete with your friends
                </h3>
                <div className="flex flex-wrap justify-center gap-3 lg:justify-start">
                  <Link to={user ? `/challenge-songs/${user.id}` : '/challenge-songs'}>
                    <Button className="bg-gray-800 text-white shadow-sm hover:bg-gray-700 active:bg-gray-900 active:shadow-inner">
                      Take a challenge
                    </Button>
                  </Link>
                  <Link to={'/songs'}>
                    <Button className="border border-amber-200 text-gray-700 bg-white hover:bg-amber-50">
                      Practice a song
                    </Button>
                  </Link>
                  <Link to={'/freeplay'}>
                    <Button className="border border-amber-200 text-gray-700 bg-white hover:bg-amber-50">
                      Free play
                    </Button>
                  </Link>
                </div>
              </div>
              <div className="flex justify-center lg:justify-end">
                <div className="w-full rounded-2xl shadow-[0_18px_40px_rgba(17,24,39,0.35)]">
                  <FeaturedSongsPreview className="w-full" />
                </div>
              </div>
            </div>
          </div>
        </div>
        <div className="bg-paper bg-amber-50/70">
          <div className="mx-auto w-full max-w-(--breakpoint-lg) px-6 py-16">
            <Leaderboard />
          </div>
        </div>
        <div className="mt-auto">
          <MarketingFooter />
        </div>
      </div>
    </>
  )
}

function Button({
  children,
  style,
  className,
}: {
  children?: React.ReactNode
  style?: React.CSSProperties
  className?: string
}) {
  return (
    <button
      className={className}
      style={{
        transition: 'background-color 150ms',
        cursor: 'pointer',
        fontSize: 'clamp(0.875rem, 0.875rem + 0.35vw, 1.05rem)',
        padding: '8px 16px',
        borderRadius: 10,
        fontWeight: 500,
        minWidth: 'max-content',
        ...style,
      }}
    >
      {children}
    </button>
  )
}
