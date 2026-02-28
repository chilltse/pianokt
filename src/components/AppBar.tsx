import { Sizer } from '@/components'
import { useOptionalAuth } from '@/features/auth'
import type { AuthUser } from '@/features/auth'
import { LucideGithub as GitHub, Menu, Youtube } from '@/icons'
import clsx from 'clsx'
import { PropsWithChildren } from 'react'
import { Button, MenuItem, MenuTrigger, Menu as RacMenu, Separator } from 'react-aria-components'
import { Link, useLocation, useNavigate } from 'react-router'
import { Popover } from './Popover'

type NavItem = { route: string; label: string }
const navItems: NavItem[] = [
  { route: '/challenge-songs', label: 'Take a challenge' },
  { route: '/songs', label: 'Practice a song' },
  { route: '/freeplay', label: 'Free play' },
  // TODO: launch phrases.
  // { route: '/training/phrases', label: 'Training' },
  { route: '/about', label: 'About' },
]

export default function AppBar() {
  const auth = useOptionalAuth()
  const showAuth = auth !== null

  return (
    <div
      className="relative flex h-[50px] min-h-[50px] flex-col justify-center bg-white border-b border-gray-200 shadow-sm"
      style={{
        // This is a hack that accounts for the sometimes present scrollbar.
        // The 100vw includes scrollbar and the 100% does not, so we padLeft the difference.
        // Credit goes to: https://aykevl.nl/2014/09/fix-jumping-scrollbar
        paddingLeft: 'calc(100vw - 100%)',
      }}
    >
      <div className="mx-auto flex w-full items-center justify-center pl-6 md:max-w-(--breakpoint-lg)">
        <div className="absolute top-1/2 right-5 left-5 z-10 -translate-y-1/2 md:hidden">
          <SmallWindowNav />
        </div>
        <Link to={'/'} className="flex items-center text-gray-700 hover:text-gray-900">
          <img
            src="/images/logo.png"
            alt="PIANO KT"
            width={35}
            height={35}
            className="object-contain"
          />
          <Sizer width={8} />
          <span className="text-2xl font-extralight">PIANO KT</span>
        </Link>
        <div className="hidden grow justify-evenly gap-6 pl-16 align-baseline whitespace-nowrap md:flex">
          {navItems.map((nav) => {
            const to =
              nav.route === '/challenge-songs' && auth?.user
                ? `/challenge-songs/${auth.user.id}`
                : nav.route
            return (
              <NavItem
                to={to}
                key={nav.label}
                label={nav.label}
                activeClassName="bg-gray-100 text-gray-900"
              />
            )
          })}
          <div className="ml-auto flex items-center gap-3 pr-8 lg:pr-0">
            {showAuth && (
              <>
                {auth?.user ? (
                  <AccountButton user={auth.user} />
                ) : (
                  <>
                    <NavItem to="/login" label="Log in" />
                    <NavItem to="/register" label="Sign up" />
                  </>
                )}
              </>
            )}
            <NavIconButton
              to={'https://www.youtube.com/@%E8%B0%A2%E9%A3%9E%E6%9C%BA-z3q'}
              label="YouTube"
              title="YouTube"
            >
              <Youtube size={20} />
            </NavIconButton>
            <NavIconButton
              to={'https://github.com/chilltse/sightread'}
              label="GitHub"
              title="GitHub"
            >
              <GitHub size={20} />
            </NavIconButton>
          </div>
        </div>
      </div>
    </div>
  )
}

function SmallWindowNav() {
  const navigate = useNavigate()
  const auth = useOptionalAuth()
  return (
    <MenuTrigger>
      <Button aria-label="Open menu" className="inline-flex">
        <Menu height={24} width={24} className="block text-gray-700" />
      </Button>
      <Popover className="w-[min(90vw,360px)] rounded-2xl border border-gray-200 bg-white p-2 shadow-xl">
        <RacMenu className="outline-none">
          {navItems.map((nav) => {
            const to =
              nav.route === '/challenge-songs' && auth?.user
                ? `/challenge-songs/${auth.user.id}`
                : nav.route
            return (
              <MenuItem
                key={nav.label}
                onAction={() => navigate(to)}
                className={clsx(
                  'flex w-full items-center rounded-xl px-3 py-2 text-base font-medium text-gray-700 transition outline-none',
                  'data-[focused]:bg-gray-100 data-[pressed]:bg-gray-200',
                )}
              >
                {nav.label}
              </MenuItem>
            )
          })}
          {auth && (
            <>
              <Separator className="mx-2 my-1 border-t border-gray-200" />
              {auth.user ? (
                <MenuItem
                  onAction={() => navigate(`/account/${auth.user!.id}`)}
                  className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-base font-medium text-gray-700 transition outline-none data-[focused]:bg-gray-100 data-[pressed]:bg-gray-200"
                >
                  <span
                    className={clsx(
                      'flex h-8 w-8 shrink-0 overflow-hidden rounded-full bg-gray-200',
                      !auth.user.avatarUrl && 'flex items-center justify-center text-xs text-gray-500',
                    )}
                  >
                    {auth.user.avatarUrl ? (
                      <img src={auth.user.avatarUrl} alt="" className="h-full w-full object-cover" />
                    ) : (
                      (auth.user.displayName ?? auth.user.email ?? '?').slice(0, 1).toUpperCase()
                    )}
                  </span>
                  Account
                </MenuItem>
              ) : (
                <>
                  <MenuItem
                    onAction={() => navigate('/login')}
                    className="flex w-full items-center rounded-xl px-3 py-2 text-base font-medium text-gray-700 transition outline-none data-[focused]:bg-gray-100 data-[pressed]:bg-gray-200"
                  >
                    Log in
                  </MenuItem>
                  <MenuItem
                    onAction={() => navigate('/register')}
                    className="flex w-full items-center rounded-xl px-3 py-2 text-base font-medium text-gray-700 transition outline-none data-[focused]:bg-gray-100 data-[pressed]:bg-gray-200"
                  >
                    Sign up
                  </MenuItem>
                </>
              )}
            </>
          )}
          <Separator className="mx-2 my-1 border-t border-gray-100" />
          <MenuItem
            href="https://github.com/chilltse/sightread"
            target="_blank"
            className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium text-gray-700 transition outline-none data-[focused]:bg-gray-100 data-[pressed]:bg-gray-200"
            aria-label="GitHub"
          >
            <GitHub size={18} className="t-[2px] relative" />
            <span className="sr-only">GitHub</span>
          </MenuItem>
          <MenuItem
            href="https://www.youtube.com/@%E8%B0%A2%E9%A3%9E%E6%9C%BA-z3q"
            target="_blank"
            className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium text-gray-700 transition outline-none data-[focused]:bg-gray-100 data-[pressed]:bg-gray-200"
            aria-label="YouTube"
          >
            <Youtube size={18} />
            <span className="sr-only">YouTube</span>
          </MenuItem>
        </RacMenu>
      </Popover>
    </MenuTrigger>
  )
}

function AccountButton({ user }: { user: AuthUser }) {
  const currentRoute = useLocation().pathname
  const isActive = currentRoute === `/account/${user.id}`
  return (
    <Link
      to={`/account/${user.id}`}
      className={clsx(
        'inline-flex items-center gap-2 rounded-md px-2 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100 active:bg-gray-200',
        isActive && 'bg-gray-100 font-semibold text-gray-900',
      )}
    >
      <span
        className={clsx(
          'flex h-8 w-8 shrink-0 overflow-hidden rounded-full bg-gray-200',
          !user.avatarUrl && 'flex items-center justify-center text-xs text-gray-500',
        )}
      >
        {user.avatarUrl ? (
          <img src={user.avatarUrl} alt="" className="h-full w-full object-cover" />
        ) : (
          (user.displayName ?? user.email ?? '?').slice(0, 1).toUpperCase()
        )}
      </span>
      <span>Account</span>
    </Link>
  )
}

function NavItem(
  props: PropsWithChildren<{
    to: string
    className?: string
    label: string
    activeClassName?: string
  }>,
) {
  const currentRoute = useLocation().pathname
  return (
    <Link
      to={props.to}
      className={clsx(
        'inline-flex items-center justify-center rounded-md px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100 active:bg-gray-200',
        props.className,
        currentRoute === props.to && (props.activeClassName || 'font-bold'),
      )}
    >
      {props.label}
    </Link>
  )
}

function NavIconButton(
  props: PropsWithChildren<{ to: string; label: string; title?: string; className?: string }>,
) {
  return (
    <Link
      to={props.to}
      aria-label={props.label}
      title={props.title ?? props.label}
      className={clsx(
        'flex h-9 w-9 items-center justify-center rounded-md text-gray-600 transition-colors hover:bg-gray-100 hover:text-gray-900 active:bg-gray-200',
        props.className,
      )}
    >
      {props.children}
      <span className="sr-only">{props.label}</span>
    </Link>
  )
}
