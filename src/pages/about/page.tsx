import { AppBar, MarketingFooter, Sizer } from '@/components'
import React, { PropsWithChildren } from 'react'
import { Link, LinkProps } from 'react-router'
import manifest from './../../manifest.json'
import type { SongMetadata } from './../../types'
import { Article, CaptionedImage } from './components'
import { slugify } from './utils'

function SidebarLink({ children }: PropsWithChildren<{ children: string }>) {
  return (
    <a className="hover:text-amber-800" href={`#${slugify(children)}`}>
      {children}
    </a>
  )
}

export default function AboutPage() {
  return (
    <div className="relative bg-paper bg-amber-50/70 min-h-screen">
      <title>About</title>
      <AppBar />
      <div className="bg-paper bg-amber-50/70">
        <div className="mx-auto flex max-w-(--breakpoint-lg)">
          <div className="sticky top-0 hidden max-h-screen p-8 lg:block">
            <section className="mx-auto flex flex-col">
              <h2 className="text-3xl">About</h2>
              <Sizer height={32} />
              <ul className="flex flex-col gap-5 text-xl whitespace-nowrap">
                <li>
                  <SidebarLink>What</SidebarLink>
                </li>
                <li>
                  <SidebarLink>Getting started</SidebarLink>
                </li>
                <li>
                  <SidebarLink>Music selection</SidebarLink>
                </li>
                <li>
                  <SidebarLink>Browser compatibility</SidebarLink>
                </li>
                <li>
                  <SidebarLink>Feedback</SidebarLink>
                </li>
                <li>
                  <SidebarLink>Attributions</SidebarLink>
                </li>
              </ul>
            </section>
          </div>
          <div className="mx-auto my-8 w-full flex-1 bg-white p-8 text-base">
            <div className="mx-auto flex max-w-prose flex-col gap-12">
              <WhatSection />
              <GettingStarted />
              <MusicSelectionSection />
              <BrowserCompatibilitySection />
              <FeedbackSection />
              <AttributionsSection />
            </div>
          </div>
        </div>
      </div>
      <MarketingFooter />
    </div>
  )
}

function WhatSection() {
  return (
    <Article
      header="What"
      first="pianoKT is a free and open-source webapp for learning to play Piano."
    >
      <p>
        pianoKT is great for piano lovers, you can take a challenge and analyse the skill status and get the most fit recommendations.
        pianoKT has an intuitive <span className="italic">Falling Notes</span> visualization
        of a song, similar to rhythm games like Guitar Hero.
      </p>
      <p>
        This project is also a research project of Australian National University Social Machine Lab.
        There will be no audio recording, the midi-typing events will be recorded as midi-files.
        The midi data will be fully anonymised and used for the research purpose. Your contribution is greatly appreciated by the community and we work hard to present the research results in the future.
      </p>
      <p>
        Thanks to the Sightread team for the skeleton of the app.
      </p>
      <Sizer height={8} />
      <CaptionedImage
        src="/images/mode_falling_notes_screenshot.png"
        caption="Falling Notes with note labels"
        height={1628}
        width={1636}
        fetchPriority="high"
      />
      <Sizer height={24} />
      <p>
        For those who want to learn sheet music, PianoKT offers{' '}
        <span className="italic">Sheet Hero (beta)</span> mode. Sheet Hero is a halfway point
        between the simplicity of falling notes and the full complexity of sheet music. Notes are
        laid out on a musical staff, but timing is simplified. Sheet Hero represents the duration of
        notes with a tail instead of beat denominations. Key signatures are also optional in this
        mode. PianoKT will by default display a song in it’s original key, but you may change the
        key to any that you prefer.
      </p>
      <Sizer height={8} />
      <CaptionedImage
        src="/images/mode_sheet_hero_screenshot.png"
        width={1980}
        height={1148}
        caption="Sheet Hero (beta) with note labels"
      />
    </Article>
  )
}

function GettingStarted() {
  return (
    <Article header="Getting started" first="Plug in a keyboard. Start slow. Gradually speed up.">
      <p>
        When initially learning a song, we recommend learning left and right hands separately. You
        should also take advantage of the BPM modifier to slow down a song by at least 50%. It is
        significantly more helpful to hit the right notes with good form and slowly build up speed
        than to frantically practice at full speed and build bad habits. This is especially true
        when combining hands.
      </p>
      <p>
        If you connect a MIDI keyboard, you can enable <span className="italic">Wait</span> mode –
        the song will wait for you to hit the right key before progressing.
      </p>
      <p>
        PianoKT works best in conjunction with a Piano teacher. Falling notes will allow you to
        have more fun with less experience, but it is no replacement for formal education. Learning
        music theory will help you get a more holistic music experience than learning solely
        learning how to play songs.
      </p>
    </Article>
  )
}

function MusicSelectionSection() {
  return (
    <Article
      header="Music selection"
      first="The PianoKT catalog has two components: builtin and local file uploads."
    >
      <p>PianoKT includes music from the public domain.</p>
      <p>You can upload MIDI files directly to PianoKT which saves them in browser storage.</p>
    </Article>
  )
}

function BrowserCompatibilitySection() {
  return (
    <Article
      header="Browser compatibility"
      first="PianoKT is fully compatible with the latest versions of Chrome and Firefox."
    >
      <p>
        Plugging in a MIDI keyboard will not work on iOS or Safari. This is because Apple has not
        implemented the WebMIDI spec and also{' '}
        <AboutLink to="https://css-tricks.com/ios-browser-choice/">restricts</AboutLink> iOS devices
        from using any browser engine but their own.
      </p>
    </Article>
  )
}

function FeedbackSection() {
  return (
    <Article header="Feedback">
      <p>
        Found a bug or have a feature request? Please file an issue on{' '}
        <AboutLink to="https://github.com/chilltse/pianokt/issues">GitHub</AboutLink> or send an{' '}
        <a href="mailto:chilltse808@gmail.com" className="text-purple-primary hover:text-purple-hover">
          email
        </a>
        .
      </p>
    </Article>
  )
}

function AttributionsSection() {
  const sortedSongs = (manifest as SongMetadata[])
    .slice()
    .sort((a, b) => a.title.localeCompare(b.title))

  function MutedLink({ children, ...props }: PropsWithChildren<LinkProps>) {
    return (
      <Link
        {...props}
        className="cursor-pointer text-gray-800 underline-offset-4 hover:text-gray-950 hover:underline"
        target="_blank"
        rel="noopener noreferrer"
      >
        {children}
      </Link>
    )
  }

  return (
    <Article header="Attributions">
      <p>
        Some of the sheet music and arrangements featured on this site are based on scores shared
        through <AboutLink to="https://musescore.com">MuseScore</AboutLink> under Creative Commons
        licenses.
      </p>
      <p>
        We are grateful to the contributors. Below are links back to MuseScore and their respective
        copyrights. No modifications were made to the original arrangements.
      </p>
      <ul className="list-disc pl-6">
        {sortedSongs.map((song) => (
          <li key={song.id} className="mb-2">
            <div className="font-semibold">{song.title}:</div>
            <div className="ml-2 flex flex-wrap gap-2">
              {song.url && <MutedLink to={song.url}>[source]</MutedLink>}
              {song.license && <MutedLink to={song.license}>[license]</MutedLink>}
            </div>
          </li>
        ))}
      </ul>
    </Article>
  )
}

function AboutLink({ children, ...props }: PropsWithChildren<LinkProps>) {
  return (
    <Link {...props} className="text-purple-primary hover:text-purple-hover">
      {children}
    </Link>
  )
}
