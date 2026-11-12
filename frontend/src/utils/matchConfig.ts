import { useEffect, useState } from 'react'
import { api } from '../api'

export interface MatchConfig {
  review_threshold: number
  top_threshold: number
}

export const DEFAULT_MATCH_CONFIG: MatchConfig = { review_threshold: 80, top_threshold: 90 }

let cached: MatchConfig | null = null
let inflight: Promise<MatchConfig> | null = null

export function fetchMatchConfig(): Promise<MatchConfig> {
  if (cached) return Promise.resolve(cached)
  if (!inflight) {
    inflight = api
      .get<MatchConfig>('/api/match/config')
      .then((config) => {
        cached = config
        return config
      })
      .catch(() => {
        inflight = null
        return DEFAULT_MATCH_CONFIG
      })
  }
  return inflight
}

export function useMatchConfig(): MatchConfig {
  const [config, setConfig] = useState<MatchConfig>(cached ?? DEFAULT_MATCH_CONFIG)
  useEffect(() => {
    fetchMatchConfig().then(setConfig)
  }, [])
  return config
}
