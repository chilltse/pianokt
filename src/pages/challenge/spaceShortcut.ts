/** Capture only Challenge transport keys; interactive modals retain their keyboard behavior. */
export function createChallengeSpaceShortcut() {
  let held = false
  let consumed = false
  return {
    keydown(event: KeyboardEvent, blocked: boolean, action: () => void) {
      if (event.code !== 'Space') return
      const wasHeld = held
      held = true
      const target = event.target as HTMLElement | null
      const editing = target?.closest?.('input, textarea, select, [contenteditable="true"]')
      if (blocked || editing || event.altKey || event.ctrlKey || event.metaKey) return
      event.preventDefault()
      event.stopPropagation()
      consumed = true
      if (wasHeld || event.repeat) return
      action()
    },
    keyup(event: KeyboardEvent) {
      if (event.code !== 'Space') return
      if (consumed) {
        event.preventDefault()
        event.stopPropagation()
      }
      held = false
      consumed = false
    },
    reset() {
      held = false
      consumed = false
    },
  }
}
export const challengeKeyboardOptions = { capture: true }
