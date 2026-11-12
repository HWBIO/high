export type DataEventName = 'positions' | 'projects' | 'companies' | 'candidates' | 'tracking' | 'prompts' | 'runs'

const CHANNEL_NAME = 'hh-match:data-updated'
const browserChannel =
  typeof window !== 'undefined' && 'BroadcastChannel' in window
    ? new BroadcastChannel(CHANNEL_NAME)
    : null

export function notifyDataUpdated(name: DataEventName) {
  window.dispatchEvent(new CustomEvent(CHANNEL_NAME, { detail: name }))
  browserChannel?.postMessage(name)
}

export function onDataUpdated(names: DataEventName[], handler: () => void) {
  const allowed = new Set(names)
  const onWindow = (event: Event) => {
    const name = (event as CustomEvent<DataEventName>).detail
    if (allowed.has(name)) handler()
  }
  const onChannel = (event: MessageEvent<DataEventName>) => {
    if (allowed.has(event.data)) handler()
  }

  window.addEventListener(CHANNEL_NAME, onWindow)
  browserChannel?.addEventListener('message', onChannel)
  return () => {
    window.removeEventListener(CHANNEL_NAME, onWindow)
    browserChannel?.removeEventListener('message', onChannel)
  }
}
