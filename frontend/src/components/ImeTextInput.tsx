import { useEffect, useRef, useState } from 'react'
import type { InputHTMLAttributes } from 'react'

type ImeTextInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'> & {
  value: string
  onValueChange: (value: string) => void
}

export function ImeTextInput({ value, onValueChange, onCompositionStart, onCompositionEnd, onBlur, ...props }: ImeTextInputProps) {
  const composing = useRef(false)
  const [draft, setDraft] = useState(value)

  useEffect(() => {
    if (!composing.current) setDraft(value)
  }, [value])

  return (
    <input
      type="text"
      {...props}
      value={draft}
      onCompositionStart={(event) => {
        composing.current = true
        onCompositionStart?.(event)
      }}
      onCompositionEnd={(event) => {
        composing.current = false
        const next = event.currentTarget.value
        setDraft(next)
        onValueChange(next)
        onCompositionEnd?.(event)
      }}
      onChange={(event) => {
        const next = event.currentTarget.value
        setDraft(next)
        if (!composing.current) onValueChange(next)
      }}
      onBlur={(event) => {
        if (draft !== value) onValueChange(draft)
        onBlur?.(event)
      }}
    />
  )
}
