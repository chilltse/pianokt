/// <reference lib="dom" />
/// <reference lib="dom.iterable" />

declare module '*.md?raw' {
  const content: string
  export default content
}

interface Window {
  gtag: (event: string, action: string, object: any) => void
}
