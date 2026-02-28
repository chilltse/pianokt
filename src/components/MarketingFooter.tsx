import { cn } from '@/utils'
import React, { PropsWithChildren } from 'react'
import Sizer from './Sizer'

function MaxWidthWrapper(props: PropsWithChildren<{ as?: any; className?: string }>) {
  const className = (props.className ?? '') + ' max-w-(--breakpoint-lg) mx-auto px-8'
  const Component = props.as ?? 'div'
  return <Component className={className}>{props.children}</Component>
}

export function MarketingFooter() {
  return (
    <footer
      className="bg-paper bg-amber-50/70 w-full border-t border-amber-100"
      aria-labelledby="footer-heading"
    >
      <h2 id="footer-heading" className="sr-only">
        Footer
      </h2>
      <MaxWidthWrapper className="mx-auto w-full py-4">
        <div className="text-gray-600 text-center text-xs sm:text-left">
          © Adapted from the Sightread Project by PianoKT Project, LLC.
        </div>
      </MaxWidthWrapper>
    </footer>
  )
}
